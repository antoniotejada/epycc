#!/usr/bin/env python
"""
epycc - Embedded Python C Compiler

Copyright (C) 2021 Antonio Tejada

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
Extended filesystem for transparently reading files inside archives as if they
were directories
"""

# See ISO/IEC 9899:1999 Annex A (aka C99) or ISO/IEC 9899:TC2 6.4.1 
import re
import string
import StringIO


class ClassStruct:
    """
    Memory lightweight class
    """
    __slots__ = ["field1", "field2"]
    def __init__(self, field1, field2):
        self.field1 = field1
        self.field2 = field2

class Struct:
    """
    C-like struct

    use with 
    my_struct = Struct(field1=value1, field2=value2)
    my_struct.field1 = blah

    new fields can also be added after the fact with
    my_struct.field3 = blah
    """
    def __init__(self, **kwds):
        self.__dict__.update(kwds)
    def __repr__(self):
        # XXX This may be making the debugger stall when there are lots of 
        #     linked states?
        s = []
        for key in self.__dict__:
            s += ["%s: %s" % (key, self.__dict__[key])]
        return string.join(s, ", ")

def vrb(*args):
    if (False):
        print (args)

def dbg(*args):
    print(args)


def get_first(s):
    return next(iter(s))

def is_empty(l):
    return (len(l) == 0)

def get_char(state):
    # Right now this does a simple preprocessing by removing \\n
    # XXX Do other standard C preprocessing (macros, etc)
    # XXX Keep track of file row and col
    c = None
    if (state.prec != ''):
        c = state.prec
        state.prec = ''

    else:
        while (c is None):
            c = state.f.read(1)
            if (c == '\\'):
                prec = c
                c = state.f.read(1)
                if (c == '\n'):
                    c = None

                else:
                    state.prec = prec

    print "get_char: ", c
    return c

# XXX Could generate numba python to make simpler codegen?
def get_token(state):
    s = state.c
    m = r.match(s)
    mm = None
    while (m is not None):
        # XXX This could read in chunks
        c = get_char(state)
        if (c == ''):
            return None, None
        # print 'read', repr(c)
        s += c
        mm = m
        m = r.match(s)

    if (mm is None):
        # Unrecognized token
        print "unable to match", repr(s)
        return Struct(token = None, value = s)
    m = mm
    state.c = c
    return Struct( token = m.lastgroup, value = m.group(0))


def parse_grammar(grammar_text):
    """
    Parse a text extracted from the ISO/IEC 9899:1999 pdf Annex "A.1 Lexical
    grammar" or "A.2 Phrase structure grammar"
    """
    symbols = {}
    current_symbol = None
    for l in grammar_text:
        if (is_empty(l)):
            # Allow and ignore empty lines
            print "ignoring empty line"
            
        # New symbol starts with parens, otherwise comment
        elif (l[0] == "("):
            # New symbol
            # (6.4.2.1) identifier:
            # (6.4.2.1) nondigit: one of
            m = re.match(r"\([^)]*\)\s+(?P<name>[^:]+):\s*((?P<none_of>none of)|(?P<one_of>one of))?", l)
            assert None is dbg("found symbol", l)
            symbol_name = m.group("name")
            one_of = m.group("one_of") is not None
            none_of = m.group("none_of") is not None
            current_symbol = Struct(name=symbol_name, one_of=one_of, none_of=none_of, rules=[])
            assert current_symbol not in symbols
            symbols[symbol_name] = current_symbol

        elif (l[0].isspace()):
            # New rules for the current symbol start with whitespace, eg
            #     identifier-nondigit
            #     identifier identifier-nondigit
            #     identifier digit
            # XXX Support continuing previous symbol if indentation is larger?
            #     (the spec does that in two or three rules)
            assert None is dbg("found rule", l)
            rule_symbols = re.split(r"\s+", l)
            if (not(none_of or one_of)):
                current_symbol.rules.append([])
            for rule_symbol in rule_symbols:
                rule_symbol = rule_symbol.decode('string_escape')
                if (rule_symbol == "opt"):
                    # Opt applies to the previous symbol
                    current_symbol.rules[-1][-1].opt = True

                elif (rule_symbol != ""):
                    # split can return empty strings, ignore those
                    # "One of" symbols create one rule per rule_symbol
                    if (none_of or one_of):
                        # XXX We only support none of single chars, for
                        #     multichar it could be implemented with recursion,
                        #     eg the regexp for none of "*/" is
                        #     r"/\*(([^*]*)\*[^/]*)*\*/" and likewise for nfas
                        assert (not none_of) or (len(rule_symbol) == 1)
                        current_symbol.rules.append([])
                    current_symbol.rules[-1].append(Struct(symbol=rule_symbol, opt=False))
                
                    
        else:
            # Comment
            print "ignoring comment", l

    return symbols

def build_regexp(symbols, terminal_symbols, symbol_name):
    """
    XXX Many of these are not correct and just doodles

    A: a            A: a opt        A: a b          A: a opt b
    r(A) = a        r(A) = a?       r(A) = ab       r(A) = a?b

    A: a            A: a            A: B
       b               b            B: b
    r(A) = "a|b"    r(A) = "a|b"    r(A) = r(B) = b

    A: B C                  A: B opt C
    B: b                    B: b
    C: c                    C: c
    r(A) = r(B)r(C) = bc    r(A) = r(B)?r(C) = b?c

    A: B                        A: B opt
       C                           C
    B: b                        B: b
    C: c                        C: c
    r(A) = r(B)|r(C) = a|b      r(A) = r(B)?|r(C) = b?|c

    A: a                            A: a
       A b                             A b c 
    r(A) = a|r(A)b = ... = a(b)*    r(A) = a(bc)*

    A: a                            A: a
       A b                             A b c 
       A c                             A d e
    r(A) = a|r(A)b|r(A)c = ... = a|a(b|c)* = (a)(b|c)*    

    
    A: a
       b c A
    r(A) = (bc)*a

    A: a
       b A c
    r(A) = ??

    A: a
       B
    B: A
    r(A) = a|r(B) = a|r(A) = 

    A: one of       A: none of
       a               a 
       b               b 
    r(A) = a|b      r(A) 0 [^ab]

    A: one of
      ab
      cd
    r(A) = ab|cd

    A: none of
      ab
    r(A) = (a[^b]|[^a].)



    """
    symbol = symbols[symbol_name]
    rs = []
    rs_recursive = []
    for rule in symbol.rules:
        ss = ""
        recursive = False
        for rule_symbol in rule:
            if (rule_symbol.symbol in terminal_symbols):
                # Don't escape one_of or none_of, since they will be escaped
                # after put into sets below and need to be unescaped for the set
                # calculation
                if (symbol.one_of or symbol.none_of):
                    ss += rule_symbol.symbol
                else:
                    ss += re.escape(rule_symbol.symbol)
            else:
                if (rule_symbol.symbol == symbol_name):
                    # XXX This doesn't support more than one recursion inside
                    #     the same rule (ie must be strictly left recursive)
                    assert(not recursive)
                    # XXX Only left recursive grammars supported
                    assert(ss == "")
                    # XXX Recursive opts not supported
                    assert(not rule_symbol.opt)
                    recursive = True
                else:
                    # Use non-named groups since there's a limit of 100 named
                    # groups in Python's re module
                    sss = "(?:%s)" % build_regexp(symbols, terminal_symbols, rule_symbol.symbol)
                    if (rule_symbol.opt):
                        sss = "%s?" % sss
                    ss += sss
            
        if (recursive):
            rs_recursive.append(ss)
        else:    
            rs.append(ss)

    if (len(rs_recursive) > 0):
        # XXX This probably wrong in the general case, it's assuming left
        #     recursive (will break for right or middle-recursive)
        assert(not symbol.one_of and not symbol.none_of)
        s = "(?:%s)(?:%s)*" % (string.join(rs, "|"), string.join(rs_recursive, "|"))
        
    else:
        if (symbol.one_of or symbol.none_of):
            # Compress multiple single chars into set, consecutive into ranges
            new_rs = []
            ranges = []
            prev = None
            first = None
            for r in sorted(rs):
                if (len(r) == 1):
                    if (prev is None):
                        ranges.append(re.escape(r))
                        first = r
                        
                    else:
                        if ((ord(r[0]) - ord(prev[0])) == 1):
                            ranges[-1] = "%s-%s" % (first, re.escape(r))

                        else:
                            if (ord(first[0]) - ord(prev[0]) == 1):
                                # Don't use ranges for just two chars in the
                                # range
                                ranges[-1] = re.escape(first)
                                ranges.append(re.escape(prev))
                            ranges.append(re.escape(r))
                            first = r

                    prev = r

                else:
                    new_rs.append(re.escape(r))

            if (len(ranges) > 0):
                if (symbol.none_of):
                    # XXX This only accepts "none of" for single chars, otherwise
                    #     it's ill defined? what does "none of" */ mean, how many
                    #     chars should it match?
                    assert(len(new_rs) == 0)
                    # invert the set
                    fmt = "[^%s]"
                else:
                    fmt = "[%s]"
                
                ranges = fmt % string.join(ranges, "")
                new_rs.append(ranges)
                
            rs = new_rs

        s = string.join(rs, "|")

    # Add the symbol name as debugging info
    add_debug_info = False
    if (add_debug_info):    
        # XXX This could use named groups (?P<name>expr) but note that the group
        #     name must be a valid python identifier (eg cannot contain "-") and
        #     a group name can only be defined once, but there's one of this for
        #     every symbol with a rule that uses this symbol_name, so the oher
        #     occurrences have to be with some incremental count
        s = "(?:(?#%s)%s)" % (symbol_name, s)
    else:
        s = "%s" % s
        
    return s


def create_transition(target, charset=set(), negated=False):
    transition = Struct(target=target, charset=set(charset), negated=negated)
    return transition

def create_state(name, final = False, transitions = []):
    state = Struct(name=name, transitions=list(transitions), final=final)
    
    return state

def create_nfa(first, last):
    nfa = Struct(first=first, last=last)
    return nfa


# XXX Most of the following build_xxx_nfa functions generate lots of spurious
#     lambda transitions for simplicity, remove the unnecessary ones?

def build_string_nfa(s):
    # Create first and last dummy states and the nfa for this string
    first_state = create_state(name="%s-first" % s)
    last_state = create_state(name="%s-last" % s)
    nfa = create_nfa(first_state, last_state)

    # Create one state per char, linking the first dummy state to the first
    # char state with a lambda
    prev_c = []
    prev_state = first_state
    for c in s:
        state = create_state("%s-%c" % (s, c))
        prev_state.transitions.append(create_transition(state, set(prev_c)))
        prev_c = [c]
        prev_state = state

    # Link last char to last dummy state
    prev_state.transitions.append(create_transition(nfa.last,set(prev_c)))

    return nfa

def build_charset_nfa(name, charset, negated):
    # Create first and last dummy states and the nfa for this charset
    first_state = create_state(name="%s-first" % name)
    last_state = create_state(name="%s-last" % name)
    nfa = create_nfa(first_state, last_state)
    state = create_state("%s" % name)

    first_state.transitions.append(create_transition(state))
    state.transitions.append(create_transition(last_state, charset, negated))
    
    return nfa


def build_recursive_nfa(name, sub_nfa):
    # Link first to last and last to first
    first_state = create_state(name="%s-recursive-first" % name)
    last_state = create_state(name="%s-recursive-last" % name)
    nfa = create_nfa(first_state, last_state)
    
    # Link the incoming nfa to the new first and last states
    first_state.transitions.append(create_transition(sub_nfa.first))
    sub_nfa.last.transitions.append(create_transition(last_state))

    # Link the last to first and first to last
    first_state.transitions.append(create_transition(last_state))
    last_state.transitions.append(create_transition(first_state))
    
    return nfa


def build_concatenation_nfa(nfas):
    for i, sub_nfa in enumerate(nfas[:-1]):
        sub_nfa.last.transitions.append(create_transition(nfas[i+1].first))

    return create_nfa(nfas[0].first, nfas[-1].last)


def build_union_nfa(name, nfas):
    # Create first and last dummy states and the nfa for this string
    first_state = create_state(name="%s-union-first" % name)
    last_state = create_state(name="%s-union-last" % name)
    nfa = create_nfa(first_state, last_state)

    for sub_nfa in nfas:
        nfa.first.transitions.append(create_transition(sub_nfa.first))
        sub_nfa.last.transitions.append(create_transition(nfa.last))
        
    return nfa

def build_optional_nfa(nfa):
    # connect first and last states
    # XXX Should check there's not a link already?
    nfa.first.transitions.append(create_transition(nfa.last))

    return nfa

def find_lambda_transitions(first_state):
    visited_states = set()
    pending_states = set([first_state])
    # XXX Should this be a set? the lambda_transition are wrapper objects so it's
    #     unlikely testing for belonging, etc is useful
    lambda_transitions = []

    while (len(pending_states) > 0):
        state = pending_states.pop()
        # Mark early as visited, don't want to add to pending below if it has
        # loops
        visited_states.add(state)
        for t in state.transitions:
            if (is_empty(t.charset)):
                # XXX This assert shouldn't be necessary, it's just to check
                #     if there are direct loops
                assert(t.target is not state)
                assert t not in set([lt.transition for lt in lambda_transitions])
                lambda_transitions.append(Struct(source=state, transition=t))
            if (t.target not in visited_states):
                pending_states.add(t.target)
        
    return lambda_transitions

def remove_lambda_transitions(nfa):
    lambda_transitions = find_lambda_transitions(nfa.first)

    first_states = set([nfa.first])
    while (len(lambda_transitions) > 0):
        lambda_transition = lambda_transitions.pop(0)
        source, transition = lambda_transition.source, lambda_transition.transition
        target = transition.target
        # Remove the transition
        assert None is vrb(len(lambda_transitions), ": removing transition id", id(transition), "from", source.name, "to", target.name)
        source.transitions.remove(transition)
         
        if (source is target):
            continue
        # append the target transitions to the source, updating the lambda
        # transitions table if necessary
        for t in target.transitions:
            t = create_transition(t.target, t.charset, t.negated)
            source.transitions.append(t)
            if (is_empty(t.charset)):
                assert t not in set([lt.transition for lt in lambda_transitions])
                lambda_transitions.append(Struct(source=source, transition=t))

        # XXX Remove duplicates in some other way? Have transitions be a set?
        source.transitions = list(set(source.transitions))

        # mark source as final if target is final
        if (target.final):
            source.final = True
        # mark target as initial if source is initial
        if (source in first_states):
            first_states.add(target)

    # XXX This can generate equivalent states, find a way to remove them?

    return first_states


def find_lambda_closure(state):
    # lambda closure of a state is that state plus the states it can reach using
    # only lambda transitions
    lambda_closure = set([state])
    pending_states = set([state])
    while (len(pending_states) > 0):
        state = pending_states.pop()
        for t in state.transitions:
            if (is_empty(t.charset) and (t.target not in lambda_closure)):
                pending_states.add(t.target)
                lambda_closure.add(t.target)

    return lambda_closure


def nfa_to_dfa(first_state):
    """
    Take a lambda NFA/eNFA (NFA with epsilon moves) and convert to DFA.

    The states of the DFA will be lambda closures of NFA states.
    The transitions of the DFA will be transitions between those the
    states in those closures.

    1. Set as initial DFA state the lambda closure of the initial NFA state
    2. For each character in the input alphabet, collect the NFA states each
       NFA state in that closure transitions to (ie non-lambda transitions).
       Create a new DFA state with the closure of those NFA states.
    3. 
    4. Continue until there are no new states added to the NFA
    5. Mark as final any DFA state that includes a final state of the NFA
    """
    # XXX This should be done finding the lambda closures for all the states
    #     reusing the dependent lambda closure information
    def lookup_or_find_lambda_closure(lambda_closures, state):
        lambda_closure = lambda_closures.get(state, None)
        if (lambda_closure is None):
            lambda_closure = find_lambda_closure(state)
            lambda_closures[state] = lambda_closure

        # We don't need this as a set anymore, convert to tuple for ease of
        # hashing into dictionaries, other sets
        return tuple(lambda_closure)

    lambda_closures = {}
    closure_to_dfa_state = {}

    first_lambda_closure = lookup_or_find_lambda_closure(lambda_closures, first_state)

    # Set a new DFA state as the closure of the NFA state
    dfa_state =  create_state(
        # XXX Pick another way of naming the state?
        first_lambda_closure[0].name,
        # Final if any state in the lambda closure is final
        # XXX This should calculate whether the closure is final when generating
        #     it rather than after the fact
        any([_.final for _ in first_lambda_closure])
    )
    # XXX Cache this tuple?
    closure_to_dfa_state[first_lambda_closure] = dfa_state

    pending_closures = [first_lambda_closure]
    while (len(pending_closures) > 0):
        lambda_closure = pending_closures.pop()
        dfa_state = closure_to_dfa_state[lambda_closure]
        
        # Find out the transitions of this DFA state
        # the transitions of the closure are the closure of the transitions
        # let
        #  - d() as the transition set
        #  - alphabet symbol a
        #  - nfa states q1, q2, ..., qn
        #  - dfa state A={q1, q2}
        # d(A, a) = closure(d({q1, q2}, a)) = closure(d(q1, a) U d(q2, a)) 

        used_chars = set()
        char_to_targets = {}
        for nfa_state in lambda_closure:
            # Collect all the transitions per char
            for t in nfa_state.transitions:
                if (is_empty(t.charset)):
                    # Ignore lambda transitions
                    continue 
                charset = t.charset
                if (t.negated):
                    # Convert from negated charset to regular
                    # XXX There should be a better way of doing this by union of 
                    #     negated and union of non-negated?
                    # XXX At least we should be able to not expand if all the 
                    #     transitions use negated charsets or all the transitions
                    #     use non-negated?
                    charset = [chr(i) for i in xrange(256) if chr(i) not in t.charset]
                
                used_chars.update(charset)
                for c in charset:
                    try:
                        char_to_targets[c].add(t.target)
                    except KeyError:
                        char_to_targets[c] = set([t.target])

        # Create a DFA state for every char target, ideally merging transitions
        # that go to the same target
        target_dfa_state_to_transition = {}
        for c in used_chars:
            # Calculate the closure of the target for this char
            targets = char_to_targets[c]
            targets_closure = set()
            for target in targets:
                target_closure = lookup_or_find_lambda_closure(lambda_closures, target)
                targets_closure.update(target_closure)

            # XXX Cache this tuple?
            tuple_targets_closure = tuple(targets_closure)
            target_dfa_state = closure_to_dfa_state.get(tuple_targets_closure, None)
            if (target_dfa_state is None):
                # This is a new state, create it and add to pending
                # Add the closure of targets as new DFA state
                target_dfa_state = create_state(
                    # XXX Pick another way of naming the state?
                    tuple_targets_closure[0].name,
                    # Final if any state in the lambda closure is final
                    any([_.final for _ in tuple_targets_closure])
                )
                pending_closures.append(tuple_targets_closure)
                closure_to_dfa_state[tuple_targets_closure] = target_dfa_state
                
            # Add the transition to the state or merge if there's already a
            # transition to that dfa state
            target_dfa_transition = target_dfa_state_to_transition.get(target_dfa_state, None)
            if (target_dfa_transition is None):
                # Create a new one
                target_dfa_transition = create_transition(target_dfa_state, set(), False)
                target_dfa_state_to_transition[target_dfa_state] = target_dfa_transition
                dfa_state.transitions.append(target_dfa_transition)

            target_dfa_transition.charset.add(c)
            
        # Compact the charset by turning into negated if more than 128 elements
        for t in dfa_state.transitions:
            if (len(t.charset) > 128):
                negated_charset = [chr(i) for i in xrange(256) if chr(i) not in t.charset]
                t.negated = True
                t.charset.clear()
                t.charset.update(negated_charset)
        
    return closure_to_dfa_state[first_lambda_closure]


def build_dot(first_states):
    def dot_escape(s):
        # Graphviz needs to escape the chars:
        #     - can't contain "
        #     - can't contain \
        #     - can't contain the usual non-printable chars
        # Use string_escape plus manual " and \\ escaping
        s = s.encode("string_escape")
        s = s.replace("\\", "\\\\")
        s = s.replace("\"", "\\\"")
        
        return s

    l = [ "digraph graphname {" ]

    pending_states = set(first_states)
    visited_states = set()
    while (len(pending_states) > 0):
        state = pending_states.pop()
        visited_states.add(state)
        
        node_attribs = ['label="%s"' % dot_escape(state.name)]
        if (state.final):
            # Use double lines for final states
            node_attribs.append("peripheries=2")

        # Use id's as nodes since different nodes can have the same name when
        # used in different parts of the grammar
        l.append('%d [%s];' % (id(state), string.join(node_attribs, ",")))
    
        for t in state.transitions:
            # label the edge with lambda, single char or the list of chars
            if (is_empty(t.charset)):
                # &lambda; works for html viewer but gives missing entity erro
                # on svg and verbatim in png, use the &# encoding instead which
                # works on all
                edge_label =  "&#955;"

            elif ((len(t.charset) == 1) and not t.negated):
                edge_label = dot_escape(str(get_first(t.charset)))

            else:
                # try to compress the charset
                prev = None
                ranges = []
                for c in sorted(t.charset):
                    if (prev is None):
                        ranges.append(dot_escape(c))
                        first = c
                        
                    else:
                        if ((ord(c[0]) - ord(prev[0])) == 1):
                            ranges[-1] = "%s-%s" % (
                                dot_escape(first),
                                dot_escape(c)
                            )

                        else:
                            if (ord(first[0]) - ord(prev[0]) == 1):
                                # Don't use ranges for just two chars in the
                                # range
                                ranges[-1] = dot_escape(first)
                                ranges.append(dot_escape(prev))
                            ranges.append(dot_escape(c))
                            first = c

                    prev = c

                # XXX This could ignore the incoming negated value and pack the
                #     set as negated/non-negated if it has more than 128 chars?
                if (t.negated):
                    # invert the set
                    fmt = "[^%s]"
                else:
                    fmt = "[%s]"
                
                edge_label = fmt % string.join(ranges, "")
                
            # Create the edge using the ids as node identifiers
            l.append('%d->%d [label="%s"];' % (id(state), id(t.target), edge_label))
            if (t.target not in visited_states):
                pending_states.add(t.target)

    l.append("}")
    return string.join(l, "\n")


def build_nfa(symbols, terminal_symbols, symbol_name):

    assert None is dbg("build_nfa", symbol_name)

    symbol = symbols[symbol_name]

    if (symbol.one_of or symbol.none_of):
        # if all are single chars can use charset nodes, otherwise need to use
        # string nodes
        # one of and none of rules only have one symbol rule per rule
        assert all([len(rule) == 1 for rule in symbol.rules])
        ones = [rule[0].symbol for rule in symbol.rules]
        assert all([one in terminal_symbols for one in ones])
        if (max([len(one) for one in ones]) == 1):
            nfa = build_charset_nfa(symbol.name, ones, symbol.none_of)

        else:
            nfas = []
            for one in ones:
                nfas.append(build_string_nfa(one))

            nfa = build_union_nfa(symbol.name, nfas)

    else:
        recursive_nfas = []
        opt_recursive_nfas = []
        union_nfas = []

        has_left_recursion = False
        has_right_recursion = False
        for rule in symbol.rules:
            concatenation_nfas = []
            recursive_rule = False
            opt_recursive_rule = False
            for rule_symbol in rule:

                if (rule_symbol.symbol == symbol_name):
                    # recursion
                    # XXX Not supported:
                    #     - middle recursion
                    #     - multiple recursion in the same rule
                    #     - mixed left and right recursion across rules of the same symbol
                    assert(not recursive_rule)
                    recursive_rule = True
                    opt_recursive_rule = rule_symbol.opt
                    if (rule[0].symbol == symbol_name):
                        # Don't support mixing left and right recursion in the
                        # same symbol
                        assert(not has_right_recursion)
                        has_left_recursion = True

                    elif (rule[-1].symbol == symbol_name):
                        # Don't support mixing left and right recursion in the
                        # same symbol
                        assert(not has_left_recursion)
                        has_right_recursion = True

                    else:
                        # Middle recursion, not supported
                        assert False
                
                else:
                    if (rule_symbol.symbol in terminal_symbols):
                        nfa = build_string_nfa(rule_symbol.symbol)
                        
                    else:
                        nfa = build_nfa(symbols, terminal_symbols, rule_symbol.symbol)

                    if (rule_symbol.opt):
                        nfa = build_optional_nfa(nfa)
                
                    concatenation_nfas.append(nfa)
                
            # Don't support empty rules
            assert(not is_empty(concatenation_nfas))
            nfa = build_concatenation_nfa(concatenation_nfas)

            if (recursive_rule):
                if (opt_recursive_rule):
                    opt_recursive_nfas.append(nfa)
                else:
                    recursive_nfas.append(nfa)
            else:
                union_nfas.append(nfa)

        nfa = build_union_nfa(symbol.name, union_nfas)
        if (not is_empty(recursive_nfas) or not is_empty(opt_recursive_nfas)):
            # right recursion     left recursion    opt right rec   mixed opt
            # A:                  A:                A:              A:
            #   a                   a                 a               a
            #   b                   b                 b               b
            #   c A                 A c               c A opt         c A
            #   d A                 A d               d A opt         d A opt
            # (c|d)*(a|b)         (a|b)(c|d)*       (c|d)*(a|b)?    c*(a|b|d)|d*(a|b|d)?
            
            # XXX This should actually be done by creating the necessary
            #     transitions which is a lot simpler and more powerful than
            #     trying to generate the regexp and work out the NFA from there

            # 
            # XXX Mixing opt and non-opt requires a deep copy of the union nfa,
            #     not supported by now
            assert(is_empty(recursive_nfas) or is_empty(opt_recursive_nfas))
            if (not is_empty(recursive_nfas)):
                recursive_nfa = build_recursive_nfa(symbol.name, build_union_nfa(symbol.name, recursive_nfas))
            
            elif (not is_empty(opt_recursive_nfas)):
                recursive_nfa = build_recursive_nfa(symbol.name, build_union_nfa(symbol.name, opt_recursive_nfas))
                nfa = build_optional_nfa(nfa)
            
            if (has_left_recursion):
                nfa = build_concatenation_nfa([nfa] + [recursive_nfa])

            else:
                assert(has_right_recursion)
                nfa = build_concatenation_nfa([recursive_nfa] + [nfa])
                
    return nfa


def build_test_nfa(symbols, terminal_symbols):
    sub_nfas = []
    for keyword in ["char", "int", "case", "break", "breakage"]:
        sub_nfa = build_string_nfa(keyword)
        sub_nfas.append(sub_nfa)

    sub_nfa = build_concatenation_nfa(
        [   
            build_string_nfa("s"), 
            build_recursive_nfa(build_string_nfa("tar"))
        ]
    )
    sub_nfas.append(sub_nfa)

    sub_nfa = build_charset_nfa("identifier", set(string.letters + string.digits))
    sub_nfas.append(sub_nfa)

    nfa = build_union_nfa("keyword", sub_nfas)
    nfa.last.final = True

    return nfa


def get_token_nfa(scanner_state):
    f = scanner_state.f
    # Set to zero to disable the cache
    # With a 32 length and a 0 length cache for j2k.c timeit reports 7x (and
    # most of the 2 secs time is generating and dumping the initial nfa)
    #   1 loops, best of 3: 2.89 sec per loop
    #   1 loops, best of 3: 19.4 sec per loop
    max_active_state_cache_key_length = 32
    max_active_state_cache_keys = 1024
    
    active_states = set(scanner_state.first_states)
    active_state_cache = scanner_state.active_state_cache
    buffer = scanner_state.buffer
    match_length = 0
    match_state = None
    i = 0
    while (True):
        # Read from the file or from the buffer

        # Need to store in buffer so we can backtrack to the end of the longest
        # match when the nfa has no ore active states to navigate to (another
        # option may be to always seed the active states with the initial states
        # so we are always matching each substring against the nfa, but looks
        # more cumbersome and probably even slower)
        if (i >= len(buffer)):
            c = f.read(1)
            # XXX Missing tracking file/buffer row/col position

            # XXX Missing eating backslash newline, multiline comments, but
            #     watching out for double quoted strings maybe with escaped
            #     quotes inside

            if (c == ""):
                # If there's a match, return that one and prepare the buffer
                # to return any leftovers for next time, if any
                if (match_state is not None):
                    match_word = buffer[:match_length]
                    scanner_state.buffer = buffer[match_length:]
                    
                    return match_state, match_word

                elif (scanner_state.buffer != ""):
                    # EOF and leftovers
                    leftover = scanner_state.buffer
                    scanner_state.buffer = ""
                    return None, leftover
                
                else:
                    # EOF and no leftovers
                    return None

            buffer += c

        else:
            c = buffer[i]
        
        s = buffer[0:i+1]
        # XXX This could read more than one char and then do binary search on
        #     the cache
        if ((len(s) < max_active_state_cache_key_length) and 
            (s in active_state_cache)):

            assert None is vrb("cache hit for", repr(s))
            cache_entry = active_state_cache[s]
            new_active_states = cache_entry.active_states
            match_length = cache_entry.match_length
            match_state = cache_entry.match_state

        else:
            # Navigate the nfa

            assert None is vrb("cache miss for", repr(s))
            # XXX Verify that symbols are returned in priority order (eg
            #     keywords before identifiers)
            new_active_states = set()
            for state in active_states:
                # print "checking", state.name
                # XXX Could have a transition cache
                for transition in state.transitions:
                    assert(not is_empty(transition.charset))
                    if ((c in transition.charset) ^ transition.negated):
                        # c is in the non negated charset or is not in the
                        # negated charset
                        new_active_states.add(transition.target)
                        if (transition.target.final):
                            match_length = i + 1
                            match_state = transition.target.name
            
            # Don't bother inserting cache keys that are too long, they are 
            # probably comments or strings that won't be reused
            if (len(s) <= max_active_state_cache_key_length):
                # Evict keys if over the max entries
                if (len(active_state_cache) == max_active_state_cache_keys):
                    # Don't bother keeping LRU with an OrderedDict since it's
                    # shown to be slower than just random and a regular dict (6s
                    # to LRU insertions and deletions, 4s to LRU deletions, 2.8s
                    # no LRU at all and using regular dict)
                    active_state_cache.popitem()

                active_state_cache[s] = Struct(active_states=new_active_states, 
                    match_length=match_length, match_state=match_state)
                

        assert None is vrb("active states", len(new_active_states))
        if (is_empty(new_active_states)):
            # Nowhere to advance the nfa, return token if matched, erro token
            # otherwise
            if (match_length == 0):
                match_length = 1
            
            match_word = buffer[:match_length]
            assert None is dbg("matched" if match_state is not None else "unmatched", match_state, ":", repr(match_word))
                
            scanner_state.buffer = buffer[match_length:]
            
            return match_state, match_word

        else:
            i += 1
        
        active_states = new_active_states


def get_token_dfa(scanner_state):
    f = scanner_state.f
    # Set to zero to disable the cache
    # With a 32 length and a 0 length cache for j2k.c timeit reports 7x (and
    # most of the 2 secs time is generating and dumping the initial nfa)
    #   1 loops, best of 3: 2.89 sec per loop
    #   1 loops, best of 3: 19.4 sec per loop
    max_active_state_cache_key_length = 32
    max_active_state_cache_keys = 1024
    
    active_state = scanner_state.first_state
    active_state_cache = scanner_state.active_state_cache
    buffer = scanner_state.buffer
    match_length = 0
    match_state = None
    i = 0
    while (True):
        # Read from the file or from the buffer

        # Need to store in buffer so we can backtrack to the end of the longest
        # match when the nfa has no ore active states to navigate to (another
        # option may be to always seed the active states with the initial states
        # so we are always matching each substring against the nfa, but looks
        # more cumbersome and probably even slower)
        if (i >= len(buffer)):
            c = f.read(1)
            # XXX Missing tracking file/buffer row/col position

            # XXX Missing eating backslash newline, multiline comments, but
            #     watching out for double quoted strings maybe with escaped
            #     quotes inside

            if (c == ""):
                # If there's a match, return that one and prepare the buffer
                # to return any leftovers for next time, if any
                if (match_state is not None):
                    match_word = buffer[:match_length]
                    scanner_state.buffer = buffer[match_length:]
                    
                    return match_state, match_word

                elif (scanner_state.buffer != ""):
                    # EOF and leftovers
                    leftover = scanner_state.buffer
                    scanner_state.buffer = ""
                    return None, leftover
                
                else:
                    # EOF and no leftovers
                    return None

            buffer += c

        else:
            c = buffer[i]
        
        s = buffer[0:i+1]
        # XXX This could read more than one char and then do binary search on
        #     the cache
        if ((len(s) < max_active_state_cache_key_length) and 
            (s in active_state_cache)):
            

            assert None is vrb("cache hit for", repr(s))
            scanner_state.cache_hits += 1
            cache_entry = active_state_cache[s]
            new_active_state = cache_entry.active_state
            match_length = cache_entry.match_length
            match_state = cache_entry.match_state
            

        else:
            # Navigate the nfa
            

            assert None is vrb("cache miss for", repr(s))
            scanner_state.cache_misses += 1
            # XXX Verify that symbols are returned in priority order (eg
            #     keywords before identifiers)
            new_active_state = None
            state = active_state

            # print "checking", state.name
            # XXX Could have a transition cache
            # XXX For DFA this could be a lot faster by having a direct lookup
            #     instead of iterating transitions
            for transition in state.transitions:
                assert(not is_empty(transition.charset))
                if ((c in transition.charset) ^ transition.negated):
                    # c is in the non negated charset or is not in the
                    # negated charset
                    new_active_state = transition.target
                    if (transition.target.final):
                        match_length = i + 1
                        match_state = transition.target.name
        
            # Don't bother inserting cache keys that are too long, they are 
            # probably comments or strings that won't be reused
            if (len(s) <= max_active_state_cache_key_length):
                # Evict keys if over the max entries
                if (len(active_state_cache) == max_active_state_cache_keys):
                    # Don't bother keeping LRU with an OrderedDict since it's
                    # shown to be slower than just random and a regular dict (6s
                    # to LRU insertions and deletions, 4s to LRU deletions, 2.8s
                    # no LRU at all and using regular dict)
                    active_state_cache.popitem()

                active_state_cache[s] = Struct(active_state=new_active_state, 
                    match_length=match_length, match_state=match_state)
                

        if (new_active_state is None):
            # Nowhere to advance the nfa, return token if matched, error token
            # otherwise
            if (match_length == 0):
                match_length = 1
            
            match_word = buffer[:match_length]
            assert None is dbg("matched" if match_state is not None else "unmatched", match_state, ":", repr(match_word))
                
            scanner_state.buffer = buffer[match_length:]
            
            return match_state, match_word

        else:
            i += 1
        
        active_state = new_active_state


def print_nfa_stats(first_states):
    
    print "first states:", len(first_states)

    pending_states = set(first_states)
    visited_states = set()
    num_states = 0
    num_transitions = 0
    num_lambda_transitions = 0
    while (not is_empty(pending_states)):
        state = pending_states.pop()
        visited_states.add(state)
        num_states += 1
        for t in state.transitions:
            num_transitions += 1
            if (is_empty(t.charset)):
                num_lambda_transitions += 1
            if (t.target not in visited_states):
                pending_states.add(t.target)
                
    print "total states:", num_states
    print "total transitions:", num_transitions, "avg", num_transitions * 1.0 / num_states
    print "lambda transitions:", num_lambda_transitions, "avg", num_lambda_transitions * 1.0 / num_states
    

def create_nfa_scanner(first_states, f):
    return Struct(f=f, first_states=first_states, active_state_cache=dict(), 
        buffer="", cache_misses = 0, cache_hits = 0)

def create_dfa_scanner(first_state, f):
    return Struct(f=f, first_state=first_state, active_state_cache=dict(), 
        buffer="", cache_misses = 0, cache_hits = 0)

def scan_nfa(first_states, filepath):
    with open(filepath, "r") as f:
        scanner = create_nfa_scanner(first_states, f)
        while (True):
            token = get_token_nfa(scanner)
            if (token is None):
                print "EOF"
                break
            else:
                assert None is dbg(token)
    
def scan_dfa(first_state, filepath):
    with open(filepath, "r") as f:
        scanner = create_dfa_scanner(first_state, f)
        while (True):
            token = get_token_dfa(scanner)
            if (token is None):
                print "EOF"
                break
            else:
                assert None is dbg(token)
        print "char cache hits", scanner.cache_hits, "char cache misses", scanner.cache_misses


def main():
    with open("grammars/c99_lexical_grammar.txt", "r") as f:
        c99_lexical_grammar = f.read().splitlines()
    scanner_symbols = parse_grammar(c99_lexical_grammar)
    
    if (True):
        with open("grammars/c99_phrase_structure_grammar.txt", "r") as f:
            c99_phrase_structure_grammar = f.read().splitlines()
        parser_symbols = parse_grammar(c99_phrase_structure_grammar)
     
    assert None is dbg(scanner_symbols)
    print "terminal symbols"
    scanner_terminal_symbols = set()
    for symbol_name, symbol in scanner_symbols.items():
        for rule in symbol.rules:
            for rule_symbol in rule:
                if rule_symbol.symbol not in scanner_symbols:
                    scanner_terminal_symbols.add(rule_symbol.symbol)
    assert None is dbg(scanner_terminal_symbols)

    #dbg(build_regexp(scanner_symbols, scanner_terminal_symbols, "block-comment"))
    # grammar_regexp =  build_regexp(scanner_symbols, scanner_terminal_symbols, "token")
    
    # Using regexp directly has two problems:
    # - in the Python re module there's no incremental matching, you have to
    #   pass the whole string to re and find a match. Other options would be to
    #   convert the regexp to one that accepts partial matches
    # - in the Python re module and most other regexp engines, the longest match
    #   is guaranteed in the presence of star (unless non-greedy is used), but
    #   not in the face of alternative regexps, eg analyzing 1.2 would yield a
    #   match for 1 as an integer constant instead of a longer mach of 1.2 as a
    #   floating point constant
    
    nfa = build_nfa(scanner_symbols, scanner_terminal_symbols, "token")
    #nfa = build_nfa(scanner_symbols, scanner_terminal_symbols, "translation-unit")
    nfa.last.final = True
    generate_dot = True
    if (generate_dot):
        dot = build_dot([nfa.first])
        with open("_out/before.dot", "w") as f:
            f.write(dot)
    print_nfa_stats([nfa.first])

    dfa = nfa_to_dfa(nfa.first)
    if (generate_dot):
        dot = build_dot([dfa])
        with open("_out/dfa.dot", "w") as f:
            f.write(dot)
    print_nfa_stats([dfa])

    first_states = remove_lambda_transitions(nfa)
    if (generate_dot):
        dot = build_dot(first_states)
        with open("_out/after.dot", "w") as f:
            f.write(dot)
    print_nfa_stats(first_states)
    
    
    do_perf = False
    if (do_perf):
        import timeit
        t = timeit.Timer(lambda: scan_dfa(dfa, "_out/pngvalid.c"), setup='print("dfa")')
        print(t.timeit(5))

        import timeit
        t = timeit.Timer(lambda: scan_nfa([dfa], "_out/pngvalid.c"), setup='print("dfa_nfa")')
        print(t.timeit(5))

        t = timeit.Timer(lambda: scan_nfa(first_states, "_out/pngvalid.c"), setup='print("nfa")')
        print(t.timeit(5))

    else:
        scan_dfa(dfa, "_out/pngvalid.c")


                
if (__name__ == "__main__"):
    main()
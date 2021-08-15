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
# See 
# Packrat parsers can support left recursion
# http://web.cs.ucla.edu/~todd/research/pepm08.pdf
# Packrat Parsing: Simple, Powerful, Lazy, Linear Time
# https://pdos.csail.mit.edu/~baford/packrat/icfp02/packrat-icfp02.pdf
# Lexer Hack
# https://en.wikipedia.org/wiki/Lexer_hack
# C99 Yacc and Lex
# http://www.quut.com/c/ANSI-C-grammar-l-1999.html
# http://www.quut.com/c/ANSI-C-grammar-y-1999.html
# https://stackoverflow.com/questions/44141686/how-to-make-c-language-context-free
import re
import string
import StringIO
from cstruct import Struct


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
    first_state = create_state(name="%s" % s)
    last_state = create_state(name="@%s#last" % s)
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
    first_state = create_state(name="%s" % name)
    last_state = create_state(name="@%s#last" % name)
    nfa = create_nfa(first_state, last_state)
    state = create_state("%s" % name)

    first_state.transitions.append(create_transition(state))
    state.transitions.append(create_transition(last_state, charset, negated))
    
    return nfa


def build_recursive_nfa(name, sub_nfa):
    # Link first to last and last to first
    first_state = create_state(name="@%s-recursive#first" % name)
    last_state = create_state(name="@%s-recursive#last" % name)
    nfa = create_nfa(first_state, last_state)
    
    # Link the incoming nfa to the new first and last states
    first_state.transitions.append(create_transition(sub_nfa.first))
    sub_nfa.last.transitions.append(create_transition(last_state))

    # Link the last to first and first to last
    first_state.transitions.append(create_transition(last_state))
    last_state.transitions.append(create_transition(first_state))
    
    return nfa


def build_concatenation_nfa(name, nfas):
    if (len(nfas) == 1):
        return nfas[0]

    # Link first to last and last to first
    first_state = create_state(name="@%s#concatenation-first" % name)
    last_state = create_state(name="@%s#concatenation-last" % name)
    nfa = create_nfa(first_state, last_state)

    for i, sub_nfa in enumerate(nfas[:-1]):
        sub_nfa.last.transitions.append(create_transition(nfas[i+1].first))

    # Link first to first 
    first_state.transitions.append(create_transition(nfas[0].first))
    # Link last to last
    nfas[-1].last.transitions.append(create_transition(last_state))

    return nfa


def build_union_nfa(name, nfas):
    # Create first and last dummy states and the nfa for this string
    first_state = create_state(name="@%s#union-first" % name)
    last_state = create_state(name="@%s#union-last" % name)
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
    # XXX Pick the appropriate name:
    # - If the closure has a final state, use the name of that final state
    # - Otherwise avoid picking a fake node (starting with @) if possible
    final_state_node_names = [_.name for _ in first_lambda_closure if _.final ]
    non_fake_node_names = [_.name for _ in first_lambda_closure if not(_.name.startswith("@"))]
    if (is_empty(final_state_node_names)):
        if (is_empty(non_fake_node_names)):
            dfa_state_name = first_lambda_closure[0].name
        else:
            dfa_state_name = non_fake_node_names[0]
    else:
        # XXX Should this pick the non fake if several exist?
        dfa_state_name = final_state_node_names[0]
    dfa_state =  create_state(
        dfa_state_name,
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

def create_nfa_builder(symbols, terminal_symbols):
    return Struct(nfa_stack = dict(), symbols = symbols, terminal_symbols = terminal_symbols)

def build_nfa(nfa_builder, symbol_name):
    # XXX This works for lexical grammars, but the phrase structure grammar has
    #     a lot of recursion which causes combinatory explosion when inlining
    #     the eNFAs, since every path ends up inlining the ~50 NFAs of the
    #     grammar
    #     It needs some way of referring to smaller eNFAs by reference rather
    #     than reworking and inlining the smaller eNFA all over again. 
    #     Unfortunately it's not clear how using those eNFAs by reference can be
    #     preserved when doing eNFA->NFA or eNFA->DFA conversions, maybe some
    #     kind of closure can be obtained from the NFA by ref?

    assert None is dbg("build_nfa", symbol_name)

    symbols = nfa_builder.symbols
    terminal_symbols = nfa_builder.terminal_symbols
    nfa_stack = nfa_builder.nfa_stack

    print "stack depth", len( nfa_stack.keys())
    if (symbol_name in nfa_stack):
        return nfa_stack[symbol_name]

    symbol = symbols[symbol_name]

    if (symbol.one_of or symbol.none_of):
        # "one of" and "none of" rules only have one symbol rule per rule, the
        # symbol is a string or a charset
        
        assert all([len(rule) == 1 for rule in symbol.rules])
        ones = [rule[0].symbol for rule in symbol.rules]
        assert all([one in terminal_symbols for one in ones])
        
        # XXX Grammars are normally good in using "one of" and "none of" rules
        #     but it's possible they should use them and they don't, have the 
        #     grammar parser catch that case and convert into "one of"/"none of"?
        
        # XXX This assumes "one of" and "none of" are used only on terminal
        #     symbols, probably the grammar parser should check that

        # if all are single chars can use charset nodes, otherwise need to use
        # string nodes
        if (max([len(one) for one in ones]) == 1):
            nfa = build_charset_nfa(symbol.name, ones, symbol.none_of)

        else:
            nfas = []
            for one in ones:
                nfas.append(build_string_nfa(one))

            nfa = build_union_nfa(symbol.name, nfas)

        # These don't need to update the stack since they are terminal

    else:
        # Create the first and last states even if they are dummy and push the
        # nfa to the the stack early, this allows handling the rules much more
        # easily (especially in the presence of direct and indirect recursion)
        # at the expense of a less compact NFA (but it can be compacted later
        # either by removing lambda transitions or converting to DFA)
        first_state = create_state(name="%s" % symbol_name)
        last_state = create_state(name="@%s#last" % symbol_name)
        nfa = create_nfa(first_state, last_state)
        nfa_stack[symbol_name] = nfa

        union_nfas = []
        for rule in symbol.rules:
            concatenation_nfas = []
            for rule_symbol in rule:

                if (rule_symbol.symbol in terminal_symbols):
                    concatenation_nfa = build_string_nfa(rule_symbol.symbol)
                    
                else:
                    concatenation_nfa = build_nfa(nfa_builder, rule_symbol.symbol)
                    
                if (rule_symbol.opt):
                    concatenation_nfa = build_optional_nfa(concatenation_nfa)
            
                concatenation_nfas.append(concatenation_nfa)
                
            # Don't support empty rules
            # XXX Check this at the grammar parsing level?
            assert(not is_empty(concatenation_nfas))
            union_nfa = build_concatenation_nfa(symbol.name, concatenation_nfas)

            union_nfas.append(union_nfa)

        all_nfa = build_union_nfa(symbol.name, union_nfas)
        
        # Hook to first and last
        nfa.first.transitions.append(create_transition(all_nfa.first))
        all_nfa.last.transitions.append(create_transition(nfa.last))
                
        # del nfa_stack[symbol_name]
    return nfa

def build_checkable_grammar(symbols, terminal_symbols, start_symbol):
    """
    Build a grammar that can be checked at http://smlweb.cpsc.ucalgary.ca/start.html
    Following the format at http://smlweb.cpsc.ucalgary.ca/readme.html
        head -> RHS1
        | RHS2
        ....
        | RHSn.
    """
    long_to_short = {}
    def escape_checkable(s):
        if s not in long_to_short:
            if (s[0].isupper()):
                long_to_short[s] = "%s%03d" % (s[:3], len(long_to_short))
            else:
                long_to_short[s] = "a%03d" % len(long_to_short)
        s = long_to_short[s]
        return s

    def escape_non_terminal(s):
        # Non-terminal uppercase
        return escape_checkable(s.upper())

    def escape_terminal(s):
        # Terminal go in lowercase
        return escape_checkable(s.lower())

    l = []
    for symbol_name, symbol in symbols.items():
        # symbols separated by "\n"
        for i, rule in enumerate(symbol.rules):
            ll = []
            # rules in a symbol by "|" or -> if it's the first rule
            if (i == 0):
                ll.append("%s -> " % escape_non_terminal(symbol_name))
            else:
                ll.append("  | ")
            
            for rule_symbol in rule:
                # rule symbols by " "
                if (rule_symbol.symbol in terminal_symbols):
                    s = escape_terminal(rule_symbol.symbol)
                
                else:
                    s = escape_non_terminal(rule_symbol.symbol)
                ll.append(s)

            if (i == (len(symbol.rules) - 1)):
                ll.append(".")

            l.append(string.join(ll, " "))
            

    l.append("S -> %s." % escape_non_terminal(start_symbol))  
    
    grammar = string.join(l, "\n")

    return grammar


def build_lark_grammar(symbols, terminal_symbols, start_symbol):
    """
    Return the grammar in Lark format, rules in lowercase, terminals in
    uppercase.

    Rule prefixes like ! and ? can be used to affect the tree generation
    (removing intermediate nodes, tokens, etc), but are not necessary just for
    parsing itself

    see https://lark-parser.readthedocs.io/en/latest/grammar.html
    see https://lark-parser.github.io/ide/#

        start: value

        value: object
            | array
            | string
            | SIGNED_NUMBER
            | "true"
            | "false"
            | "null"

        array  : "[" [value ("," value)*] "]"
        object : "{" [pair ("," pair)*] "}"
        pair   : string ":" value

        WHITESPACE: (" " | /\t/ | /\n/ )+

        %ignore WHITESPACE
    """
    long_to_short = {}
    def escape_lark(s):
        return s

    def escape_non_terminal(s):
        # Non-terminal lowercase
        return escape_lark(s.lower().replace("-", "_"))

    def escape_terminal(s):
        # Terminal uppercase or in quotation marks
        return '"%s"' % escape_lark(s).encode("string_escape").replace('"', '\\"')

    l = []
    for symbol_name, symbol in symbols.items():
        # symbols separated by "\n"
        for i, rule in enumerate(symbol.rules):
            ll = []
            # rules in a symbol separated by "|" or : if it's the first rule
            if (i == 0):
                ll.append("%s: " % escape_non_terminal(symbol_name))
            else:
                ll.append("  | ")
            
            for rule_symbol in rule:
                # rule symbols separated by " "
                if (rule_symbol.symbol in terminal_symbols):
                    s = escape_terminal(rule_symbol.symbol)
                
                else:
                    s = escape_non_terminal(rule_symbol.symbol)
                if (rule_symbol.opt):
                    s = s + "?"
                ll.append(s)

            l.append(string.join(ll, " "))
            

    # Add a few generic rules/terminals/directives
    l.append("start: %s" % escape_non_terminal(start_symbol))
    # XXX These should be taken from the scanner?
    l.extend([
        r"WHITESPACE: (/ / | /\t/ | /\n/ | /\r/ )+", 
        "%ignore WHITESPACE"
    ])
    
    grammar = string.join(l, "\n")

    return grammar


def build_test_nfa(symbols, terminal_symbols):
    sub_nfas = []
    for keyword in ["char", "int", "case", "break", "breakage"]:
        sub_nfa = build_string_nfa(keyword)
        sub_nfas.append(sub_nfa)

    sub_nfa = build_concatenation_nfa(
        "test",
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



def find_terminal_symbols(symbols):
    print "terminal symbols"
    
    terminal_symbols = set()
    for symbol_name, symbol in symbols.items():
        for rule in symbol.rules:
            for rule_symbol in rule:
                if (rule_symbol.symbol not in symbols):
                    terminal_symbols.add(rule_symbol.symbol)
    assert None is dbg(terminal_symbols)

    return terminal_symbols


def parse_top_down(symbols, start_symbol, program_tokens):
    def create_stack_entry(symbol_name, rule_index, rule_symbol_index, rule_symbol_sub_index, token_index):
        return Struct(
            symbol_name=symbol_name, rule_index=rule_index, 
            rule_symbol_index=rule_symbol_index, rule_symbol_sub_index=rule_symbol_sub_index,
            token_index=token_index
        )
    
    def is_terminal(symbol):
        return symbol not in symbols

    def rule_str(symbol, rule_index, rule_symbol_index = 0):
        return "%s[%d] : %s" % (
            symbol.name, 
            rule_index,
            string.join(
                [rule_symbol.symbol for rule_symbol in symbol.rules[rule_index][:rule_symbol_index]] +
                ["^"] +
                [rule_symbol.symbol for rule_symbol in symbol.rules[rule_index][rule_symbol_index:]],
                " "
            )
        )

    def parse_symbol(parser, symbol):
        """
        Return false if the symbol failed to be parsed


        Issues with top down parsers:
        - When memoizing you need to use the token index and the full recursive
          symbol stack as key, using the token index and the failed symbol is
          not enough, eg

            type-name: ID 
            type-qual: ID 
            identifier: ID 
            parameter: type-name identifier
            parameter: type-name type-qual identifier
            parameters: parameter
            parameters: parameter , parameters
            
          Looks like this is what it's called "non-deterministic grammar" and
          is not supposed to be handled by packrat
          This can be solved by doing both of
          - enlarging the memoing key to contain all the 
          symbols in the descent path and not just the final symbol
          - not memoing successes, only failures

        - Left recursivity 
            A: Aa
               a
            A: Ba
               a
            B: Ab
            This can either be transformed to right recursivity with lambda
            (but probably won't work with indirect recursion unless all the rules
            in the grammar are changed to start with a terminal? 
            changing to right recursivity will also break left associativity
            in the grammar)
            or the call can succeed until the accepted token string doesn't grow anymore
            
            Note at least in the direct case there must be another rule for
            that symbol that is not recursive or the rule would never end, so 
            when it fails there's always one rule that can take over the failure
            in that same symbol without having to backtrack.
            
            XXX Left recursivity support is not implemented yet, probably needs
                something like described above where a recursive rule is let to
                recurse with iteratively increasing recursion depths, until there's 
                no progress reading the token string.
                
                This requires failing the recursion at some point (so the other 
                non-recursive rules in this symbol are tried and the token string
                advanced), but also being able to make the recursion not fail 
                once the max depth is increased again. It's possible the failure 
                key will need to be enlarged to store the current max recursion 
                level too
        """
        
        print "parsing token[%d]" % parser.token_index, parser.tokens[parser.token_index], "depth", parser.depth, "stack", parser.stack, "+", symbol.name
        ## print "success stack", parser.success_stack

        assert len(parser.success_stack) == parser.token_index

        max_parser_depth = 20
        if (parser.depth >= max_parser_depth):
            print "depth overflow"
            # Record the failure
            parser.failures.add(key)
            return False

        original_token_index = parser.token_index
        rule_index = 0
        while (rule_index < len(symbol.rules)):
            rule = symbol.rules[rule_index]

            parser.depth += 1
            parser.stack.append( (symbol.name, rule_index) )

            # At least one rule must succeed to parse
            res = True
            rule_failed = False
            key = (parser.token_index, tuple(parser.stack), rule_index)
            if (key in parser.failures):
                print "failing previous failure for rule", rule_str(symbol, rule_index), "with token[%d]" % parser.token_index, parser.tokens[parser.token_index]
                res = False
                rule_failed = True
                rule_symbol = rule[0]
            
            rule_symbol_index = 0
            while not rule_failed and (rule_symbol_index < len(rule)):
                
                rule_symbol = rule[rule_symbol_index]
                print "parsing token[%d]" % parser.token_index, parser.tokens[parser.token_index], "for rule", rule_str(symbol, rule_index, rule_symbol_index)

                # All the rule symbols must succeed to parse
                rule_symbol_token_index = parser.token_index
                if (is_terminal(rule_symbol.symbol)):
                    res = (parser.tokens[parser.token_index] == rule_symbol.symbol)
                    if (res):
                        assert (len(parser.success_stack) == parser.token_index)
                        # Store the success information in case it needs to be redone
                        success_stack_entry = Struct(
                            token_index=parser.token_index,
                            key=key, 
                            rule_index=rule_index, 
                            rule_symbol_index=rule_symbol_index,
                            symbol = symbol.name,
                        )
                        parser.success_stack.append(success_stack_entry)
                        # Advance the token, go to the next symbol in this rule
                        parser.token_index += 1
                        if (parser.token_index == len(parser.tokens)):
                            # XXX This can probably be simplified since it's
                            #     always the last terminal of the start rule
                            print "Parsed full tokens!!!"
                            return True
                            
                else:
                    
                    res = parse_symbol(parser, symbols[rule_symbol.symbol])

                    if (parser.token_index == len(parser.tokens)):
                        return True

                if (not res):
                    if (rule_symbol.opt):
                        # Failing on optional rule_symbols is ok, just restore
                        # the token to the previous symbol and continue
                        
                        # XXX What if this optional has a rule that was tagged
                        #     as failure for a given token index because of
                        #     something this optional did?
                        parser.token_index = rule_symbol_token_index
                        if (parser.token_index != len(parser.success_stack)):
                            print "popping success entries", parser.success_stack[parser.token_index:]
                            del parser.success_stack[parser.token_index:]
                            assert (parser.token_index == len(parser.success_stack))

                        # Tag as success in case this optional symbol is the
                        # last one in the rule
                        res = True
                        
                    else:
                        # Go to the next rule, token will be restored below if
                        # needed
                        break

                rule_symbol_index += 1

            if (res):
                # One rule succeeded, return to the parent with the updated
                # token
                print "token[%d]" % (parser.token_index-1), repr(parser.tokens[parser.token_index-1]), "succeeded on rule", rule_str(symbol, rule_index, rule_symbol_index)
                parser.stack.pop()
                parser.depth -= 1
                break

            else:
                # Note we may not have a rule_symbol if the rule failed early, 
                print "token[%d]" % parser.token_index, repr(parser.tokens[parser.token_index]), "failed on rule", rule_str(symbol, rule_index, rule_symbol_index)

                # Restore the parser token, try the next rule
                parser.token_index = original_token_index

                # Update the success stack
                if (parser.token_index != len(parser.success_stack)):
                    print "popping success entries", parser.success_stack
                    # Tag the last success as failure so it gets retried, pop
                    # the other successes but don't tag them as failure as the
                    # last success may have other rules that may still set the
                    # other as successes
                    success_stack_entry = parser.success_stack[-1]
                    assert len(parser.success_stack)-1 == success_stack_entry.token_index
                    success_key = success_stack_entry.key
                    parser.failures.add(success_key)
                    del parser.success_stack[parser.token_index:]
                    assert (parser.token_index == len(parser.success_stack))    
                    # Force to retry this rule 
                    rule_index -= 1

            rule_index += 1

            parser.stack.pop()
            parser.depth -= 1

        if (not res):
            # record the failure if not already
            parser.failures.add(key)
        
        return res


    # Create a dummy start symbol with the EOF token
    # XXX Have a way of guaranteeing start, maybe by checking the symbol is not 
    #     there

    start_symbol = Struct(name="@start", one_of=False, none_of=False, rules=[
        [Struct(symbol=start_symbol, opt=False), Struct(symbol="", opt=False)]
    ])
    # XXX This still doesn't guarantee it doesn't collide with a terminal
    assert(start_symbol.name not in symbols)
    # Add the EOF token to the program
    # XXX Do this in some other way that doesn't require access to the full tokens
    program_tokens.append("")
    parser = Struct(
        tokens=program_tokens, token_index=0, 
        depth=0, stack=[], failures=set(), success_stack=[]
    )
    
    res = parse_symbol(parser, start_symbol)

    return res
    

def test_dfa_scanner(scanner_grammar_filepath, source_filepath, start_symbol):
    # Using regexp for a scanner instead of a NFA/DFA has two problems:
    # - in the Python re module there's no incremental matching, you have to
    #   pass the whole string to re and find a match. Other options would be to
    #   convert the regexp to one that accepts partial matches
    # - in the Python re module and most other regexp engines, the longest match
    #   is guaranteed in the presence of star (unless non-greedy is used), but
    #   not in the presence of alternative regexps, eg analyzing 1.2 would yield
    #   a match for 1 as an integer constant instead of a longer mach of 1.2 as
    #   a floating point constant
    
    with open(scanner_grammar_filepath, "r") as f:
        scanner_grammar = f.read().splitlines()
        scanner_symbols = parse_grammar(scanner_grammar)

    scanner_terminal_symbols = find_terminal_symbols(scanner_symbols)


    nfa_builder = create_nfa_builder(scanner_symbols, scanner_terminal_symbols)
    nfa = build_nfa(nfa_builder, start_symbol)
    # Assume the last state is the final one
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

    # Disable removing lambda transitions since it takes a very long time and 
    # it's not necessary once we can convert from nfa to dfa
    if (False):
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

    scan_dfa(dfa, source_filepath)


def test_topdown_parser(parser_grammar_filepath, program_tokens, start_symbol):
    with open(parser_grammar_filepath, "r") as f:
        parser_grammar = f.read().splitlines()
    parser_symbols = parse_grammar(parser_grammar)
    parser_terminal_symbols = find_terminal_symbols(parser_symbols)

    s = build_checkable_grammar(parser_symbols, parser_terminal_symbols, start_symbol)
    with open("_out/grammar.check", "w") as f:
        f.write(s)

    s = build_lark_grammar(parser_symbols, parser_terminal_symbols, start_symbol)
    with open("_out/grammar.lark", "w") as f:
        f.write(s)
    
    # Checkable says the c99 phrase structure grammar as in the spec it's not
    # LL(1), it's also ambiguous because of the "lexer hack" needed where the
    # lexer needs to look into semantic to tell between "t * x;" being a pointer
    # declaration o an expression

    parse_top_down(parser_symbols, start_symbol, program_tokens)


def test_regexp_scanner(scanner_symbols, ):
    with open(grammar_filepath, "r") as f:
        c99_lexical_grammar = f.read().splitlines()
        scanner_symbols = parse_grammar(c99_lexical_grammar)

    dbg(build_regexp(scanner_symbols, scanner_terminal_symbols, "block-comment"))
    grammar_regexp =  build_regexp(scanner_symbols, scanner_terminal_symbols, "token")


def main():

    test_dfa = False
    if (test_dfa):
        # Scanning the c99 lexical grammar using dfa works
        scanner_grammar_filepath = "grammars/c99_lexical_grammar.txt"
        source_filepath = "_out/simple.c"
        test_dfa_scanner(scanner_grammar_filepath, source_filepath, "token")

    test_topdown = True
    if (test_topdown):
        # Tests without the need of a tokenizer since the sample program
        # uses tokens directly

        # XXX C99 grammar doesn't work with top down parser because of left
        #   recursion
        grammar_filepath = "grammars/c99_phrase_structure_grammar.txt"
        sample_program_tokens = (
            "translation-unit",
            [
            "int",      # int main(int argc, char* argv[])
            "identifier",
            "(",
            "int",
            "identifier",
            ",",
            "char",
            "*",
            "identifier",
            "[",
            "]",
            ")",
            
            "{",

            "int",        # int a = 1.0 + 1.2f;
            "identifier",
            "=",
            "constant",
            "+",
            "constant",
            ";",

            "return",    # return a;
            "a",
            ";",

            "}",
            ]
        )

        # Sample grammar to test specific cases
        grammar_filepath = "tests/test_grammar.txt"
        # Right recursive works
        sample_program_tokens = ("right-recursive", ["gh","gh","gh","gh","gh","gh","gh","gh","ef"])
        # Indirect right recursive works
        sample_program_tokens = ("indirect-right-recursive", ["st", "wx", "st", "wx", "qr"])
        # Function parameters using right recursion works
        sample_program_tokens = ("function", ["ID", "ID", "(", "ID", "ID", ",", "ID", "ID", ")"])
        # XXX Left recursive doesn't work 
        sample_program_tokens = ("left-recursive", ["ab","cd","cd","cd","cd","cd","cd"])
        # XXX Indirect left recursive doesn't work
        sample_program_tokens = ("indirect-left-recursive", ["ij","op","kl","op","kl","op","kl"])
        # Tagging a success as a failure works
        sample_program_tokens = ("backtrack-success", ["ID", "ID" ])

        test_topdown_parser(grammar_filepath, sample_program_tokens[1], sample_program_tokens[0])

                
if (__name__ == "__main__"):
    main()
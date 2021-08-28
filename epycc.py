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

See
- ISO/IEC 9899:1999 Annex A (aka C99) or ISO/IEC 9899:TC2 6.4.1 
- https://en.wikipedia.org/wiki/Lexer_hack
- http://www.quut.com/c/ANSI-C-grammar-l-1999.html
- http://www.quut.com/c/ANSI-C-grammar-y-1999.html
- LLVM tutorial ported to Python https://github.com/eliben/pykaleidoscope
- https://eli.thegreenplace.net/2015/python-version-of-the-llvm-tutorial/
- https://eli.thegreenplace.net/2015/building-and-using-llvmlite-a-basic-example
- https://eli.thegreenplace.net/2017/adventures-in-jit-compilation-part-4-in-python/
- http://dev.stephendiehl.com/numpile/
- https://github.com/sdiehl/numpile/commit/353280bc6d3f14bf203924881d02963767d10efb
- https://github.com/pfalcon/pycopy
- https://github.com/sdiehl/numpile/issues/14
"""

import ctypes
import os
import re
import string
import struct

from cstruct import Struct

import lark
import llvmlite.binding as llvm
import llvmlite.ir as ir


def unpack_op_sign_names(ops):
    return [ (op.split(":")[0], op.split(":")[1]) for op in ops]

# XXX All these should be exposed in a nice way for consumers of the parser
binops = unpack_op_sign_names([
    "+:add", "-:sub", "*:mul", "/:div", "%:mod",
    "<<:lshift", ">>:rshift",
    "<:lt", "<=:lte", ">:gt", ">=:gte","==:eq", "!=:neq",
    "&:bitand", "|:bitor", "^:bitxor",
    "&&:and", "||:or",
])

unops = unpack_op_sign_names(["+:add", "-:sub", "~:bitnot", "!:not"])

int_ops = set(["|", "&", "^", "%", "<<", ">>", "!", "~"])
rel_ops = set(["<", "<=", ">", ">=","==", "!="])
logic_ops = set(["&&", "||"])
ass_ops = set(["=", "*=", "/=", "%=", "+=", "-=", "<<=", ">>=", "&=", "^=", "|="])
incr_ops = set([ "++", "--" ])

binop_sign_to_name = { binop_sign : binop_name for binop_sign, binop_name in binops }

# XXX Missing memory operators * . -> &
# XXX Missing conditional operator ? :


unspecified_integer_types = set(["_Bool", "char", "short", "int", "long", "long long"])
float_types = set(["float", "double", "long double"])
unspecified_types = unspecified_integer_types | float_types
specifiable_integer_types = unspecified_integer_types - set(["_Bool"])

# XXX Remove signed versions which map to plain anyway, and map to the non
#     specified type and standardize on unsigned and plain at symbol table
#     creation time?
integer_specifiers = set(["unsigned", "signed"])
specified_integer_types = set(
    [(integer_specif + " " + integer_type) for integer_specif in integer_specifiers for integer_type in specifiable_integer_types]
)
integer_types = unspecified_integer_types | specified_integer_types

# XXX Note on clang char is signed on x86, unsigned on ARM
unsigned_integer_types = set(["_Bool"] + [integer_type for integer_type in integer_types if "unsigned" in integer_type])
signed_integer_types = integer_types - unsigned_integer_types

# XXX Missing _Complex
non_void_types = float_types | integer_types
all_types = float_types | integer_types | set(["void"])


class SymbolTable():
    
    def __init__(self):
        # Array of scopes, one dict per scope, plus one overflow dict

        # The overflow dict is used to store function parameters from the time
        # they are found at function definition time to the time the function's
        # main block is found and the overflow becomes the current scope
        # Then the overflow becomes the current block and parameters share the
        # same scope as the function's top-level local variables.

        # Parameters have to be at the same scope as the functions' local vars
        # and local vars that redefined them will cause error (but you can 
        # redefine them in a nested scope)
        self.scope_symbols = [dict(), dict()]

    def get(self, key, default):
        # XXX Do this without exceptions
        try:
            return self.__getitem__(key)

        except KeyError:
            return default

    def __getitem__(self, key):
        assert isinstance(key, str)
        item = None
        # Search the item in all the scopes, except for overflow
        for symbols in reversed(self.scope_symbols[:-1]):
            item = symbols.get(key, None)
            if (item is not None):
                break
            
        return item

    def __setitem__(self, key, value):
        self.scope_symbols[-2][key] = value 

    def __iter__(self):
        return self.scope_symbols[-2].__iter__()

    def __len__(self):
        return len(self.scope_symbols) - 1

    def values(self):
        return self.scope_symbols[-2].values()

    def get_overflow_item(self, key):
        # Overflow should only be read from when the symbol table is at global
        # scope
        assert len(self) == 1, "Getting overflow items on a local scope!!!"
        return self.scope_symbols[-1][key]

    def set_overflow_item(self, key, value):
        # Overflow should only be write to when the symbol table is at global
        # scope
        assert len(self) == 1, "Setting overflow items on a local scope!!!"
        self.scope_symbols[-1][key] = value

    def push_scope(self):
        # The overflow now becomes the current and a new oveflow is created
        self.scope_symbols.append(dict())

    def pop_scope(self):
        self.scope_symbols.pop()
        # Clear the new overflow
        self.scope_symbols[-1] = dict()


def get_fn_name(*args):
    """
    Return a function name based on the incoming arguments: operation name, 
    result type, parameter 1 type, parameter 2 type ...
    """

    # fn = "name__result_type__a_type__b_type..."
    l = [ args[0] ]
    # Replace spaces in case of compound types ("unsigned long", etc)
    l.extend([arg.replace(" ", "_") for arg in args[1:]])
    
    return string.join(l, "__")


def get_llvm_type_ext(t):
    """
    This is used for the contract between the caller and the callee, whether
    to signextend to the architecture word size or not.
    The caller looks at the parameters ext, the callee looks at the function ext
    - The function is ext type
    - Parameters are type ext
    Looks like on x86 only i1 needs to be specified with ext since x86 can use
    8bit and 16bit values natively (other arches would need to extend before
    and/or after the call)
    
    Setting it does change the generated code on x86, causing 8-bit to 32-bit
    sign/zero extension so for now it's only set where needed for x86
    """
    c_to_irext = {
        "_Bool" : "zeroext",
    }

    return c_to_irext.get(t, "")


def get_llvm_type(t):
    """
    Return the llvm type corresponding to a C type
    """
    # XXX Use some of the existing type list or snippets to build this?
    # XXX Calling this on every IR generation is error prone, should we 
    #     store this type in the symbol and have get_llvm_result_type, etc?
    #     Or just keep the llvm type around?
    c_to_llvm_types = {
        # XXX On windows "long double" is "double", on linux it's "x86_fp80"
        "long double" : "double",
        "double" : "double",
        "float" : "float",
        "long long" :"i64",
        "signed long long" : "i64",
        "unsigned long long" : "i64", 
        # XXX This depends on LLP64/etc windows is 32-bit
        "long": "i32",
        "signed long" : "i32",
        "unsigned long" : "i32",
        "int" : "i32",
        "signed int": "i32",
        "unsigned int" : "i32",
        "short" : "i16",
        "signed short" : "i16",
        "unsigned short" : "i16",
        "char" : "i8",
        "signed char" : "i8",
        "unsigned char" : "i8",
        "_Bool" : "i1",
        "void" : "void",
    }
    # Make sure we are covering all types
    assert all((c_type in all_types) for c_type in c_to_llvm_types)
    assert all((c_type in c_to_llvm_types) for c_type in all_types)
    
    return c_to_llvm_types[t]


def get_llvmlite_type(t):
    """
    Return the llvmlite ir type corresponding to a C type
    """

    c_to_llvmlite_types  = {
        # XXX By default this is the same as double on Windows x86 instead of x86_fp80, 
        #     also llvmlite.ir doesn't support x86_fp80
        "long double" : ir.DoubleType(),
        "double" : ir.DoubleType(),
        "float" : ir.FloatType(),
        "long long" : ir.IntType(64),
        "signed long long" : ir.IntType(64),
        "unsigned long long" : ir.IntType(64), 
        # XXX This depends on LLP64/etc windows is 32-bit
        "long": ir.IntType(32),
        "signed long" : ir.IntType(32),
        "unsigned long" : ir.IntType(32),
        "int" : ir.IntType(32),
        "signed int": ir.IntType(32),
        "unsigned int" : ir.IntType(32),
        "short" : ir.IntType(16),
        "signed short" : ir.IntType(16),
        "unsigned short" : ir.IntType(16),
        "char" : ir.IntType(8),
        "signed char" : ir.IntType(8),
        "unsigned char" : ir.IntType(8),
        "_Bool" : ir.IntType(1),
        "void" : ir.VoidType(),
    }
    # Make sure we are covering all types
    assert all((c_type in all_types) for c_type in c_to_llvmlite_types)
    assert all((c_type in c_to_llvmlite_types) for c_type in all_types)

    llvmlite_type = c_to_llvmlite_types[t]

    return llvmlite_type


def get_ctype(t):
    """
    Return the Python ctype corresponding to a C type
    """
    c_to_ctypes = {
        "long double" : ctypes.c_longdouble,
        "double" : ctypes.c_double,
        "float" : ctypes.c_float,
        "long long" : ctypes.c_longlong,
        "signed long long" : ctypes.c_longlong,
        "unsigned long long" : ctypes.c_ulonglong, 
        "long": ctypes.c_long,
        "signed long" : ctypes.c_long,
        "unsigned long" : ctypes.c_ulong,
        "int" : ctypes.c_int,
        "signed int": ctypes.c_int,
        "unsigned int" : ctypes.c_uint,
        "short" : ctypes.c_short,
        "signed short" : ctypes.c_short,
        "unsigned short" : ctypes.c_ushort,
        "char" : ctypes.c_char,
        "signed char" : ctypes.c_byte,
        "unsigned char" : ctypes.c_ubyte,
        "_Bool" : ctypes.c_bool,
        "void" : None,
        # XXX Missing ctypes.c_voidp once pointers are supported
    }
    # Make sure we are covering specified types
    assert all((c_type in all_types) for c_type in c_to_ctypes)
    assert all((c_type in c_to_ctypes) for c_type in all_types)

    return c_to_ctypes[t]    


def invoke_clang(c_filepath, ir_filepath, options=""):
    # Generate the precompiled IR in irs.ll
    # clang_filepath = R"C:\android-ndk-r15c\toolchains\llvm\prebuilt\windows-x86_64\bin\clang.exe"
    clang_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out", "clang.exe")
    # For privacy reasons and since some ir files are pushed to the repo, don't
    # leak the local path in the LLVM moduleid comment of 
    cmd = R"%s -S -std=c99 -emit-llvm -mllvm --x86-asm-syntax=intel %s -o %s %s" % (
        clang_filepath,
        options,
        os.path.relpath(ir_filepath),
        os.path.relpath(c_filepath)
    )
    os.system(cmd)

def invoke_dot(filepath):
    dot_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out", "dot.exe")
    cmd = R"%s -Tpng -o %s.png %s" %(
        dot_filepath,
        os.path.relpath(filepath),
        os.path.relpath(filepath)
    )
    os.system(cmd)


def precompile_c_snippets(generated_c_filepath, generated_ir_filepath):
    """
    Generate a file containing one C function implementing every C operation and
    type.

    The generated file can then be fed to a local clang install via

        %CLANG% -mllvm -S -std=c99 --x86-asm-syntax=intel -emit-llvm -o- generated/irs.c

    and generate LLVM IR that can be read from epycc to do the runtime codegen
    (the -S is needed so clang doesn't error trying to generate object code)

    """
    print "Precompiling C snippets"

    l = []

    # Operations are done in the same type and then the result converted 
    # using a conversion function

    # XXX Should the forced cast exist? The highest ranked type is used for
    #     operations anyway at codegen time?
            
    for unop_sign, unop_name in unops:
        for c_type in non_void_types:
            if ((unop_sign in int_ops) and (c_type not in integer_types)):
                continue

            # int add__int__int(int a) { return (int) (+a); }
            fn = "%s %s(%s a) { return (%s) (%sa); }" % (
                c_type, 
                get_fn_name(unop_name, c_type, c_type),
                c_type,
                c_type,
                unop_sign,
            )

            l.append(fn + "\n")

    for binop_sign, binop_name in binops:
        for c_type in non_void_types:
            # Don't do integer-only operations (bitwise, mod) on non-integer
            # operands
            if ((binop_sign in int_ops) and (c_type not in integer_types)):
                continue

            # char add__char__char__char(char a, char b) { return (char) (a + b); }
            fn = "%s %s(%s a, %s b) { return (%s) (a %s b); }" % (
                c_type, 
                get_fn_name(binop_name, c_type, c_type, c_type),
                c_type, c_type,
                c_type,
                binop_sign,
            )

            l.append(fn + "\n")

            # Assignment operators will be done as a = a + b
                
    # Build the type conversion functions
    for res_type in non_void_types:
        for a_type in non_void_types:
            # No need to generate for the same type (but note the table is still
            # redundant for integer types since it contains eg signed int to int)
            if (a_type != res_type):
                # char cnv__char__int(int a) { return (char) a; }
                fn = "%s %s(%s a) { return (%s) a; }" % (
                    res_type, 
                    get_fn_name("cnv", res_type, a_type),
                    a_type,
                    res_type,
                )
                l.append(fn + "\n")


    # Generate the C functions file in irs.c
    with open(generated_c_filepath, "w") as f:
        f.writelines(l)

    invoke_clang(generated_c_filepath, generated_ir_filepath)
    

def convert_to_clang_irs(llvm_irs):
    """
    Convert the provided function irs into IR that matches clang as close as 
    possible so it can be easily compared vs. clang-generated IR.

    IR code generated by llvmlite IR and clang differ in register and label
    naming.
    """
    # XXX Merge with load_functions_ir ?
    # XXX This reindexing is very flaky, specifically the function header and
    #     register vs. label parsing is brittle, something better or maybe 
    #     llvmlite parse_assembly could be used?

    # Using llvmir, the llvm code can be obtained by just str(module)
    # but that generates code that makes it hard to compare against
    # clang:
    # - llvmir uses a deduplication naming convention for registers,
    #   this is not an issue for registers used in code that will be
    #   optimized, since optimizing the code will reindex them to match
    #   clang, but function parameters are never reindexed, even in
    #   optimized code
    # - llvmir declares the snippet functions with "declare", which
    #   prevents from including the functions in the same module since
    #   llvm will error out if declared functions are present in the
    #   module where they are declared (snippets could be included in a
    #   different module, though, but it's not as convenient as having
    #   everything in the same listing). 

    # Given the above, 
    # - generate code that doesn't have declares by doing str(function)
    #   on each function.
    # - reindex the llvmir register names to match clang

    # Regarding the reindexing, clang's convention is:
    # - registers 0 to N - 1 are taken by the N function parameters
    # - register N is empty (maybe used for the basic block or the
    #   return value?)
    # - registers N + 1 and beyond are used by temporaries.
    # - register names are allocated strictly monotonically and as they
    #   first appear in the instruction stream (otherwise llvm will
    #   error out) R
    #
    # Regarding labels, label references don't increment the register
    # index, but label declarations do, which means that the label index
    # is not known until declared later and a second pass is needed to
    # update the label.

    # clang
    #   br i1 %4, label %5, label %6
    #   ; <label>:5:
    # llvmlite
    #     br i1 %".8", label %"entry.if", label %"entry.endif"
    #   entry.if:
    #     %".10" = load i32, i32* %".3"
    
    # Parse the function prototype
    # define [dso_local] [zeroext|signext] i32 @"f"(i32 [returned] %".1", i32 %".2")
    # define dso_local i32 @felse(i32, i32) local_unnamed_addr #0 {
    # define dso_local i32 @felse(i32, i32) {
    # define i32 @"fif"(i32 %".1") 
    # define float @"arith_ops"(i32 %".1", i32 %".2") 
    # define i32 @felse_dangling(i32 returned %.1, i32 %.2) local_unnamed_addr #0 {
    m = re.search(r'define (?:dso_local )?(?P<llvm_type>[^@]+) @"?(?P<name>[^"]+)"?\((?P<parameters>[^)]*)\)', llvm_irs[0])
    fn = Struct(**m.groupdict())
    if (fn.parameters.strip() == ""):
        # split will return one-element list with the empty string, just return
        # the empty string
        fn.parameters = []
    else:
        fn.parameters = fn.parameters.split(",")
    
    parameter_type_names = [
        value_type_name.strip().rsplit(" ", 1) for value_type_name in fn.parameters
    ]
    
    # Initialize the reindexing table with the parameters and the empty
    # gap
    fn.parameters = []
    index_count = 0
    name_to_index = {}
    for parameter_type_name in parameter_type_names:
        if (len(parameter_type_name) == 1):
            # No names, use default parameter names %0, %1, etc
            parameter_name = '%%%d' % i

        else:
            # Use the incoming names, don't use default parameter names since
            # they may be used in the code for regular registers
            parameter_name = parameter_type_name[1]
                
        parameter_type = parameter_type_name[0]

        name_to_index[parameter_name] = "%%%d" % index_count
        fn.parameters.append(Struct(llvm_type = parameter_type, name = parameter_name))
        index_count += 1
        
    # Skip the gap between parameters and body registers
    index_count += 1

    # Since we need a second pass anyway for filling in the labels, 
    # do a two-pass for everything, first build the reindexing table,
    # then substitute everything
    # llvmliteir label declarations:
    # entry.endif:
    # llvmliteir register usage:
    # %".5" = load i32, i32* %".3"
    # %.3 = zext i1 %3 to i32
    # %spec.select = select i1 %2, i32 0, i32 %0
    # but skip label usage:
    # br i1 %".8", label %"entry.if", label %"entry.endif"
    # comments
    # forbody.preheader:                                ; preds = %entry
    # Phi node
    #  %.3.0.lcssa = phi float [ 0.000000e+00, %entry ], [ %phitmp, %forbody.preheader ]
    # XXX Register assign is what increments the register count and has to be
    #     at the beginning of the line, which simplifies things
    re_reg_label_decl = re.compile(r'(^%"?[.0-9a-z_]+"?)[, \n]|(^[^:\n]+):', re.MULTILINE)
    re_reg_label_decl_usage = re.compile(r'(%[^ ,)]+)|(^[^:]+:)')

    # Find all the labels
    llvm_ir = string.join(llvm_irs[1:], "\n")
    labels = re.findall(r"^([^:\n]+):", llvm_ir, re.MULTILINE)
    labels = set(["%%%s" % label for label in labels])

    name_to_index["%entry"] = "%%%d" % len(fn.parameters)

    # Ignore the first line with the define
    for m in re_reg_label_decl.finditer(llvm_ir):
        match_index = m.lastindex
        reg_label_name = m.group(match_index)
        if (reg_label_name not in name_to_index):
            # Substitution entry for both register usage and label
            # declaration 
            
            if (match_index == 1):
                # Ignore label usages, only care about register usages and label
                # delcarations
                if (reg_label_name not in labels):    
                    # register usage found, fill in substitution
                    # %".3" to %N
                    name_to_index[reg_label_name] = "%%%d" % index_count
                    index_count += 1
                
            elif (reg_label_name != "entry"):
                # label declaration found, fill in substitutions for
                # label usage and label declaration

                # Usage:
                # %"entry.endif" and %entry.endif to %n
                name_to_index['%%"%s"' % reg_label_name] = "%%%d" % index_count
                name_to_index['%%%s' % reg_label_name] = "%%%d" % index_count

                # Declaration
                # entry.endif: to "; <label>:5:"
                name_to_index[reg_label_name + ":"] = "; <label>:%d:" % index_count
                index_count += 1

    debug = (__name__ == "__main__")
    if (debug):
        print "before"
        print string.join(llvm_irs, "\n")

    # Perform the replacement and filter out the define, braces and
    # entry basic block label coming from llvmir since they mismatch
    # what clang produces
    reindexed_llvm_irs = []
    for l in llvm_irs:
        if (l.startswith("define")):
            # Use a define line that matches clang, eg
            #   define dso_local zeroext i16  @add__int__int__short(i32, i16 zeroext) {
            # LLVM type extension goes first in function return value, last on function parameters
            l = "define dso_local %s @%s(%s) {" % (
                fn.llvm_type,
                fn.name,
                string.join([
                    ("%s" % parameter.llvm_type)
                    for parameter in fn.parameters], ",")
            )
        
        elif (l.startswith("{") or l.startswith("entry:")):
            # Skip the entry basic block label, and the brace that was
            # already set in the define line
            continue

        else:
            # Perform the replacement for register usage, and label usage and declaration
            # Note label usage 
            #   br i1 %".8", label %"entry.if", label %"entry.endif"
            l = re_reg_label_decl_usage.sub(lambda m: name_to_index[m.group(m.lastindex)], l)

        reindexed_llvm_irs.append(l)
        
    if (debug):
        print "after"
        print string.join(reindexed_llvm_irs, "\n")

    return reindexed_llvm_irs
    

def generate_ir(generator, node):
    # XXX This should have a generate_ir and then a nested function
    #     generate_node_ir that doesn't require passing the generator every time

    def get_grandson(node, parent_indices):
        if (len(parent_indices) > 0):
            node = get_grandson(node.children[parent_indices[0]], parent_indices[1:])
        return node

    def get_tree_tokens(node):
        # XXX This could use 
        #     all_tokens = tree.scan_values(lambda v: isinstance(v, Token))
        # but it's not sure we want to be that Lark dependent yet
        tokens = []
        if (isinstance(node, lark.Token)):
            tokens = [node.value]

        else:
            for child in node.children:
                tokens.extend(get_tree_tokens(child))

        return tokens

    def is_integer_type(a_type):
        return (a_type in integer_types)

    def is_unsigned_integer_type(a_type):
        return a_type in unsigned_integer_types

    def is_signed_integer_type(a_type):
        return a_type in signed_integer_types

    def get_unspecified_type(a_type):
        if (is_integer_type(a_type)):
            # Remove unsigned or signed from the type
            a_type = a_type.replace("unsigned", "").replace("signed","").strip()
            if (a_type == ""):
                a_type = "int"

        return a_type

    def get_type_bytes(a_type):
        type_sizes = {
            "long double" : 16,
            "double" : 8,
            "float" : 4,
            "long long" : 8,
            "signed long long" : 8,
            "unsigned long long" : 8,
            # XXX This depends on LLP64 vs LP64 windows is LLP64
            "long" : 4,
            "signed long" : 4,
            "unsigned long" : 4,
            "int" : 4,
            "signed int" : 4,
            "unsigned int" : 4,
            "short" : 2,
            "signed short" : 2,
            "unsigned short" : 2,
            "char" : 1,
            "signed char" : 1,
            "unsigned char" : 1,
            "_Bool" : 1,
        }

        return type_sizes[a_type]


    def get_result_type(op_sign, a_type, b_type):
        # Types sorted by highest rank first
        # XXX Review this really matches the c99 rank or find a way of 
        #     extracting the result type of the C snippets or from some table
        #     we build out of invoking clang with all the different combinations
        # Ranks
        #  float < double < long double
        #  _Bool < char < short < int < long < long long
        # signed and unsigned have the same rank
        # the lowest ranked floating type has a higher rank than any integer
        type_ranks = {
            "long double" : 8, 
            "double" : 7,
            "float" : 6, 
            "long long" : 5,
            "signed long long" : 5,
            "unsigned long long" : 5,
            "long" : 4,
            "signed long": 4,
            "unsigned long" : 4,
            "int" : 3,
            "signed int" : 3,
            "unsigned int" : 3,
            "short" : 2,
            "signed short" : 2,
            "unsigned short" : 2,
            "char" : 1,
            "signed char" : 1,
            "unsigned char" : 1,
            "_Bool" : 0,
        }
        # Make sure we are covering all specified types
        assert all((c_type in non_void_types) for c_type in type_ranks)
        assert all((c_type in type_ranks) for c_type in non_void_types)
        
        def get_ranked_types(a_type, b_type):
            a_type_rank = type_ranks[a_type]
            b_type_rank = type_ranks[b_type]
            if (a_type_rank >= b_type_rank):
                return a_type, b_type
            else:
                return b_type, a_type
            
        def a_or_b_type_is(c_type):
            return ((a_type == c_type) or (b_type == c_type))
    

        # XXX This could prebuild the cross product table of operands and results
        #     offline so it doesn't need to check at runtime


        # See https://www.oreilly.com/library/view/c-in-a/0596006977/ch04.html
        # See 6.3 conversions of ISO/IEC 9899:1999
        # Every integer has a rank
        #  _bool < char < short < int < long < long long
        # rank(enum) = rank(compatible int type)
        # rank(signed type) = rank(unsigned type)
        # Floating point are ranked
        #  float < double < long double
        # Integer promotion:
        # - if an int can represent all ranges of a type, an int is used
        # - else an unsigned int
        # plain char signedness is implementation dependent (on clang signed on x86
        # but unsigned on arm)
        # Bool: Any type is 0 if equals to 0, otherwise 1
        # ... more conversion rules
        # Usual arithmetic conversions 6.3.1.8
        # The usual arithmetic conversions are performed implicitly for the following operators:

        # XXX Missing checking the op is one of usual arithmetic:
        #     - Arithmetic operators with two operands: *, /, %, +, and -
        #     - Relational and equality operators: <, <=, >, >=, ==, and !=
        #     - The bitwise operators, &, |, and ^
        #     - The conditional operator, ?: (for the second and third operands)

        if (a_or_b_type_is("long double")):
            res_type = "long double"

        elif (a_or_b_type_is("double")):
            res_type = "double"

        elif (a_or_b_type_is("float")):
            res_type = "float"

        else:
            # The type is integer, do integer promotions
            assert(is_integer_type(a_type))
            assert(is_integer_type(b_type))

            a_promoted_type = a_type
            b_promoted_type = b_type


            # If an int can represent all the values of the original type
            # use int otherwise unsigned int
            if (get_type_bytes(a_type) < get_type_bytes("int")):
                a_promoted_type = "int"
            if (get_type_bytes(b_type) < get_type_bytes("int")):
                b_promoted_type = "int"
            # XXX Missing 6.3.1.3 rules

            highest_ranked_type, lowest_ranked_type = get_ranked_types(a_promoted_type, b_promoted_type)
            # apply rules to promoted operands
            if (a_promoted_type == b_promoted_type):
                # Same promoted type, pick any
                res_type = a_promoted_type

            elif (is_signed_integer_type(a_promoted_type) == is_signed_integer_type(b_promoted_type)):
                # Both are signed or both are unsigned, return highest ranked type
                res_type = highest_ranked_type

            elif (is_unsigned_integer_type(highest_ranked_type)):
                # unsigned has higher or equal rank, pick unsigned
                res_type = highest_ranked_type

            elif (get_type_bytes(highest_ranked_type) > get_type_bytes(lowest_ranked_type)):
                # highest ranked type is signed and can represent all the values of the unsigned, pick signed
                res_type = highest_ranked_type

            else:
                # pick unsigned type of the signed type
                if (is_signed_integer_type(a_promoted_type)):
                    res_type = a_promoted_type
                else:
                    res_type = b_promoted_type

                if (res_type != "_Bool"):
                    res_type = "unsigned " + res_type.replace("signed", "").strip()
                
        return res_type
    
    def get_ir_ref_reg_and_type(a):
        # XXX This is wrong in the case of lvalues of complex expressions 
        #     (pointers, arrays, structs...)
        # XXX This is old code that probably doesn't make sense with llvmlite ir
        #     builders, all the variables have a ref
        
        # Get reg and type to deal with allocations
        get_ir_reg_and_type(a)

        a = generator.symbol_table[a.value]
        assert ((a.type == "variable") or (a.type == "parameter"))

        return a.ir_ref, a.ir_reg, a.value_type

    def get_ir_reg_and_type(a):
        if (a.type == "identifier"):
            sym = generator.symbol_table[a.value]
            a_type = sym.value_type
            a_ir_type = get_llvmlite_type(a_type)

            if (not hasattr(sym, "ir_ref")):
                # Always create allocas in entry block so they are available
                # everywhere even across disjoint basic blocks and don't get
                # reallocated inside loops, etc
                with generator.llvmir.builder.goto_entry_block():
                    sym.ir_ref = generator.llvmir.builder.alloca(a_ir_type)
                
                    # If it has a register it means that it has an initial value, 
                    # copy from the register into the storage
                    
                    # Note only parameters have an ir_reg without an ir_ref.
                    # Initialized variables will get the alloca correctly
                    # bubbled up, but the expression and assign initializing
                    # them will remain in the disjoint basic block

                    # XXX Should things get reset on every basic block?
                    if (hasattr(sym, "ir_reg")):
                        assert(sym.type == "parameter")
                        generator.llvmir.builder.store(sym.ir_reg, sym.ir_ref)

            # Load from the storage to a new register to make sure the register
            # value we use is uptodate            

            # XXX Loading the ref into a new reg on every access is probably
            #     overkill, we should be able to track when the existing
            #     register holding the value is uptodate? (note it's not high
            #     priority since the loads are removed anyway by the LLVM
            #     optimizer)
            # XXX On the other hand, the symbol table shouldn't store ephemeral
            #     content like ir_reg since it may be created in one basic block
            #     and not available on another (eg regs created in a "then" block
            #     are not available on "else" blocks)
            sym.ir_reg = generator.llvmir.builder.load(sym.ir_ref)
            a_ir_reg = sym.ir_reg
            
        elif (a.type == "constant"):
            a_type = a.value_type
            a_ir_type = get_llvmlite_type(a_type)
            a_ir_reg = a_ir_type(a.value)

        else:
            a_ir_reg = a.ir_reg
            a_type = a.value_type

        return a_ir_reg, a_type

    def create_function(function_name, function_type, parameters):
        fn = Struct(
            type = "function", 
            name=function_name, 
            value_type=function_type, 
            parameters=parameters
        )

        # Create the function in the IR builder
        fn_llvmlite_type = ir.FunctionType(
            get_llvmlite_type(function_type), 
            [get_llvmlite_type(parameter.value_type) for parameter in parameters]
        )
        
        fn.ir = ir.Function(generator.llvmir.module, fn_llvmlite_type, name=function_name)

        return fn
        
    def goto_unreachable_block():
        """
        Switch to an unrechable basic block to prevent errors if code is added
        after the return. Note this can happen because of adding unreachable
        code,eg

            break;
            a = 1;

        A special case of the same problem is adding branches, which will cause
        llvmlite IR to error because of trying to add two terminators. This happens
        in well-formed code likethis because the ifthen will try to branch to
        ifend, but that block already has a return terminator.

            int fdo_return(int a, int b) {
                int s = 0;
                do {
                    if (s > 1000) {
                        return s;
                    }
                    s += a;
                } while (a > b);

                return s;
            }

        Note this block will be discarded and not even compiled in unoptimized
        code.
        """
        notreached_bb = generator.llvmir.builder.function.append_basic_block("notreached")
        generator.llvmir.builder.position_at_start(notreached_bb)


    def generate_branch_ir(target):
        """
        Utility function to trap branches on terminated blocks. This should 
        never happen since we use an unreachable block, see goto_unreachable_block
        """
        if (generator.llvmir.builder.block.is_terminated):
            print "Trying to terminate already terminated block"
            print str(generator.llvmir.builder.block)
            print "to" 
            print str(target)
            print "in function"
            print str(generator.llvmir.builder.function)

            assert False

        else:
            generator.llvmir.builder.branch(target)
                
    def generate_cbranch_ir(condition, target_true, target_false):
        """
        Utility function to trap cbranches on terminated blocks. This should 
        never happen since we use an unreachable block, see goto_unreachable_block
        """
        if (generator.llvmir.builder.block.is_terminated):
            print "Trying to terminate already terminated block"
            print str(generator.llvmir.builder.block)
            print "to" 
            print str(target_true),
            print "with condition", str(condition)
            print "in function"
            print str(generator.llvmir.builder.function)

            assert False

        else:
            generator.llvmir.builder.cbranch(condition, target_true, target_false)


    def generate_extern_call_ir(generator, fn_name, res_type, arg_type_ir_regs):
            
        arg_types = arg_type_ir_regs[::2]
        arg_ir_regs = arg_type_ir_regs[1::2]
        # ir builder errors out when declaring a function more than once, keep
        # it around for the next time
        fn_ir = generator.llvmir.externs.get(fn_name, None)
        if (fn_ir is None):
            fn_llvmlite_type = ir.FunctionType(
                get_llvmlite_type(res_type), 
                [get_llvmlite_type(arg_type) for arg_type in arg_types]
            )

            fn_ir = ir.Function(generator.llvmir.module, fn_llvmlite_type, fn_name)
            generator.llvmir.externs[fn_name] = fn_ir

        res_ir_reg = generator.llvmir.builder.call(fn_ir, arg_ir_regs)

        return res_ir_reg

    def generate_call_ir(generator, fn_name, arg_ir_reg_types):
        
        fn = generator.symbol_table[fn_name]

        arg_ir_regs = []
        for (arg_ir_reg, arg_type), parameter in zip(arg_ir_reg_types, fn.parameters):
            # Convert each argument to the parameter type
            if (arg_type != parameter.value_type):
                arg_ir_reg = generate_extern_call_ir(generator, 
                    get_fn_name("cnv", parameter.value_type, arg_type), 
                    parameter.value_type, 
                    [arg_type, arg_ir_reg]
                )
            
            arg_ir_regs.append(arg_ir_reg)
        
        res_type = fn.value_type
        res_ir_reg = generator.llvmir.builder.call(fn.ir, arg_ir_regs)

        return res_ir_reg, res_type

    def generate_assign_ir(generator, a, b):
        a_ir_ref, a_ir_reg, a_type = get_ir_ref_reg_and_type(a)
        b_ir_reg, b_type = get_ir_reg_and_type(b)

        # Return the value in case it's used as part of an expression
        res_type = a_type
        res_ir_reg = b_ir_reg

        # Convert the input type to the result type
        if (b_type != res_type):
            b_ir_reg = generate_extern_call_ir(generator, 
                get_fn_name("cnv", res_type, b_type), res_type, [b_type, b_ir_reg])

        generator.llvmir.builder.store(b_ir_reg, a_ir_ref)

        gen_node = Struct(type="ir", value_type=res_type, ir_reg=res_ir_reg)

        return gen_node


    def generate_binop_ir(generator, a, b, op_sign):
        # Call the precompiled C function for this expression (these calls
        # are not a performance issue, since they were verified be inlined
        # and optimized in LLVM optimize mode).
        
        # This could be done more elegantly with llvmlite's IR builder but
        # the precompiled function takes care of C-compliant sign
        # extension/truncation of operands and result

        # XXX Investigate how error prone regarding C compliance would be using
        #     the builder without pregenerated snippets, ie doing conversion and
        #     operations through the builder and maybe pregenerating builder
        #     instructions instead of llvm.
        a_ir_reg, a_type = get_ir_reg_and_type(a)
        b_ir_reg, b_type = get_ir_reg_and_type(b)

        res_type = get_result_type(op_sign, a_type, b_type)

        # Convert the input types to the result type
        if (a_type != res_type):
            a_ir_reg = generate_extern_call_ir(generator, 
                get_fn_name("cnv", res_type, a_type), res_type, [a_type, a_ir_reg])
            
        if (b_type != res_type):
            b_ir_reg = generate_extern_call_ir(generator, 
                get_fn_name("cnv", res_type, b_type), res_type, [b_type, b_ir_reg])

        # Perform the operation in res_type
        fn_name = get_fn_name(binop_sign_to_name[op_sign], res_type, res_type, res_type)
        res_ir_reg = generate_extern_call_ir(generator, fn_name, res_type, 
            [res_type, a_ir_reg, res_type, b_ir_reg])

        gen_node = Struct(type="ir", value_type=res_type, ir_reg=res_ir_reg)

        return gen_node


    def generate_incr_ir(generator, a, op_sign, post = True):
        # Note this generates a reg with the previous value that can be 
        # returned in case of post increment/decrement
        a_ir_reg, a_type = get_ir_reg_and_type(a)

        b = Struct(type="constant", value_type=a_type, value=1)

        b = generate_binop_ir(generator, a, b, op_sign)
        gen_node = generate_assign_ir(generator, a, b)

        if (post):
            gen_node = Struct(type="ir", value_type=a_type, ir_reg=a_ir_reg)

        return gen_node


    gen_node = None
    # Node can be Token or Tree
    if (type(node) is lark.Tree):
        # XXX When there's more than one child we need to special case some
        #     of the rules so we avoid returning too much nesting or long
        #     lists that make things down the line  harder or
        #     undeterministic (recursive productions with the depth
        #     depending on the length of lists). 
        #     Is there a better way with some automated whitelist or lark 
        #     facility that doesn't require touching the .lark file? or 
        #     maybe it could be massaged at load time?
        if (node.data == "compound_statement"):
            # compound_statement:  "{" block_item_list? "}"
            generator.symbol_table.push_scope()
            gen_node = generate_ir(generator, node.children[1])
            generator.symbol_table.pop_scope()

        elif (node.data == "argument_expression_list"):
            # argument_expression_list:  assignment_expression
            # |  argument_expression_list "," assignment_expression
            # XXX Should unify all the _list?
            if (len(node.children) == 1):
                gen_node = [generate_ir(generator, node.children[0])]
            else:
                gen_node = generate_ir(generator, node.children[0])
                gen_node.append(generate_ir(generator, node.children[2]))

        elif (node.data == "postfix_expression"):
            # postfix_expression:  primary_expression
            # |  postfix_expression "[" expression "]"
            # |  postfix_expression "(" argument_expression_list? ")"
            # |  postfix_expression "." identifier
            # |  postfix_expression "->" identifier
            # |  postfix_expression "++"
            # |  postfix_expression "--"
            # |  "(" type_name ")" "{" initializer_list "}"
            # |  "(" type_name ")" "{" initializer_list "," "}"

            if (node.children[0].data == "primary_expression"):
                gen_node = generate_ir(generator, node.children[0])

            elif (node.children[1] in ["++", "--"]):
                # Perform new_a = old_a +- 1, return old_a
                gen_node = generate_ir(generator, node.children[0])
                
                op_sign = node.children[1][0]
                gen_node = generate_incr_ir(generator, gen_node, op_sign, True)

            elif (node.children[1] == "("):
                # |  postfix_expression "(" argument_expression_list? ")"
                # Function call
                gen_node = generate_ir(generator, node.children[0])
                # XXX This only supports straight identifiers, no function pointer
                #     expressions
                assert (gen_node.type == "identifier")

                fn_name = gen_node.value
                
                arg_ir_reg_types = []
                if (node.children[2] != ")"):
                    # Collect parameters
                    gen_node = generate_ir(generator, node.children[2])
                    arg_ir_reg_types = [get_ir_reg_and_type(a) for a in gen_node]

                res_ir_reg, res_type = generate_call_ir(generator, fn_name, arg_ir_reg_types)
                gen_node = Struct(type="ir", value_type=res_type, ir_reg=res_ir_reg)

            else:
                # XXX Support the rest of postfix_expression
                assert False, "Unhandled postfix_expression %s" % node

        elif (node.data == "unary_expression"):
            # unary_expression:  postfix_expression
            # |  "++" unary_expression
            # |  "--" unary_expression
            # |  unary_operator cast_expression
            # |  "sizeof" unary_expression
            # |  "sizeof" "(" type_name ")"
            if (len(node.children) == 1):
                gen_node = generate_ir(generator, node.children[0])

            elif (node.children[0] in ["++", "--"]):
                # perform new_a = old_a +- 1, return new_a
                gen_node = generate_ir(generator, node.children[1])

                op_sign = node.children[0][1]
                gen_node = generate_incr_ir(generator, gen_node, op_sign, False)
                
            else:
                assert False, "Unsupported unary_expression %s" % repr(node)
            
        elif (node.data == "cast_expression"):
            # cast_expression:  unary_expression
            #  |  "(" type_name ")" cast_expression
            

            # Always make sure expressions leave as IR nodes. 
            # This takes care of not pushing constants and identifiers up
            # XXX This could be done earlier in primary_expression
            # XXX This may make harder detecting constants upstream?
            if (len(node.children) > 1):
                gen_node = [
                    generate_ir(generator, node.children[1]),
                    generate_ir(generator, node.children[3])
                ]
                # There's a cast operator, deal with it
                res_type = gen_node[0]
                gen_node = gen_node[1]

            else:
                gen_node = generate_ir(generator, node.children[0])
                res_type = None

            a_ir_reg, a_type = get_ir_reg_and_type(gen_node)
            
            if ((res_type is not None) and (res_type != a_type)):
                res_ir_reg = generate_extern_call_ir(generator, 
                    get_fn_name("cnv", res_type, a_type), res_type, [a_type, a_ir_reg])
            else:
                res_ir_reg = a_ir_reg
                res_type = a_type

            gen_node = Struct(type="ir", value_type=res_type, ir_reg=res_ir_reg)

        elif (node.data == "primary_expression"):
            # primary_expression:  identifier
            #   |  constant
            #   |  string_literal
            #   |  "(" expression ")"

            if (len(node.children) > 1):
                gen_node = generate_ir(generator, node.children[1])
            else:
                gen_node = generate_ir(generator, node.children[0])

        elif (node.data.endswith("_expression") and (len(node.children) == 3)):
            # Cach all two operands + sign expressions

            # assignment_expression:  conditional_expression
            #   |  unary_expression assignment_operator assignment_expression
            # conditional_expression:  logical_or_expression
            #   |  logical_or_expression "?" expression ":" conditional_expression
            # ...
            # multiplicative_expression:  cast_expression
            #   |  multiplicative_expression "*" cast_expression
            #   |  multiplicative_expression "/" cast_expression
            #   |  multiplicative_expression "%" cast_expression
            
            a, op_sign, b = (
                generate_ir(generator, node.children[0]),
                generate_ir(generator, node.children[1]),
                generate_ir(generator, node.children[2]),
            )

            if (op_sign in ass_ops):
                if (len(op_sign) > 1):
                    # assing + operation, generate "a += b" as "a = a + b"
                    b = generate_binop_ir(generator, a, b, op_sign[:-1])
                    
                gen_node = generate_assign_ir(generator, a, b)
                
            else:

                gen_node = generate_binop_ir(generator, a, b, op_sign)

        elif (node.data == "expression_statement"):
            # expression_statement:  expression? ";"
            if (len(node.children) > 1):
                gen_node = generate_ir(generator, node.children[0])

        elif (node.data == "jump_statement"):
            # jump_statement:  "goto" identifier ";"
            #     |  "continue" ";"
            #     |  "break" ";"
            #     |  "return" expression? ";"
            if (node.children[0].value == "return"):
                function_name = generator.llvmir.function.name
                fn = generator.symbol_table[function_name]
                    
                if (fn.value_type == "void"):
                    assert (len(node.children) == 2)
                    generator.llvmir.builder.ret_void()

                else:
                    gen_node = generate_ir(generator, node.children[1])
                    res_ir_reg = gen_node.ir_reg
                    res_type = gen_node.value_type

                    # If the return type is different from the expression,
                    # convert
                    if (fn.value_type != res_type):
                        assert (len(node.children) == 3)
                        
                        a_type = res_type
                        a_ir_reg = res_ir_reg
                        res_type = fn.value_type
                        res_ir_reg = generate_extern_call_ir(generator, get_fn_name("cnv", res_type, a_type),
                            res_type, [a_type, a_ir_reg])
                        
                    generator.llvmir.builder.ret(res_ir_reg)
                
                goto_unreachable_block()
                
            elif (node.children[0].value == "break"):
                generate_branch_ir(generator.llvmir.break_bb)
                goto_unreachable_block()
                
            elif (node.children[0].value == "continue"):
                generate_branch_ir(generator.llvmir.continue_bb)
                goto_unreachable_block()

            else:
                # XXX Missing goto
                assert False, "Unsupported jump_statement %s" % repr(node)

            # XXX Null gen_node in this and others?

        elif (node.data == "init_declarator_list"):
            # init_declarator_list:  init_declarator
            # |  init_declarator_list "," init_declarator 
            # Skip the commas, pass the rest
            
            if (len(node.children) == 1):
                gen_node = [generate_ir(generator, node.children[0])]
            else:
                gen_node = generate_ir(generator, node.children[0])
                gen_node.append(generate_ir(generator, node.children[2]))

        elif (node.data == "function_definition"):
            # function_definition:  declaration_specifiers declarator declaration_list? compound_statement
            # function_definition
            #   declaration_specifiers
            #     type_specifier	double
            #   declarator
            #     direct_declarator
            #       direct_declarator
            #         identifier	params
            #       (
            #       parameter_type_list
            #         parameter_list
            #           parameter_list

            # Read the name, return type and parameters so we put the
            # function in the symbol table with full parameter and return
            # type information before body is generated, in case function's
            # body needs it eg because calls it recursively
            
            # Read return type
            function_type = generate_ir(generator, node.children[0])
            
            # Read name and parameters
            gen_node = generate_ir(generator, node.children[1])
            function_name = gen_node[0].value

            fn = generator.symbol_table.get(function_name, None)
            
            # Collect parameters, note they could be empty
            parameters = []
            if (gen_node[2] != ")"):
                parameters = gen_node[2]
            if (fn is None):            
                fn = create_function(function_name, function_type, parameters)
            
                generator.symbol_table[function_name] = fn 

            else:
                # XXX Do some checking if the function already exists (should be a
                #     forward declaration)
                # Override the existing parameters since the come from a forward
                # declaration and they may not have names
                fn.parameters = parameters

            # Link the parameters to the ir builder function arguments and put
            # them in the overflow symbol table
            for parameter, arg in zip(fn.parameters, fn.ir.args):
                parameter.ir_reg = arg
                generator.symbol_table.set_overflow_item(parameter.name, parameter)

            generator.llvmir.function = fn.ir

            # Give a hard-coded name that gets removed below since clang-generated
            # tests don't contain a basic block entry label
            block = generator.llvmir.function.append_basic_block("entry")
            generator.llvmir.builder = ir.IRBuilder(block)

            # Generate the function's body
            gen_node = generate_ir(generator, node.children[-1])

            # The current block won't be terminated either because 
            # - it's the unreacheable block placed after every return
            # -  the main function returns void 
            # - doesn't have returns in the main path (eg returns on the if
            #   branches but not on the endif)
            # Always return an Undefined value of the right type to prevent
            # LLVM IR errors about the block not being terminated, which happens
            # even if the block is unreacheable
            assert (not generator.llvmir.builder.block.is_terminated)
            if (fn.value_type == "void"):
                generator.llvmir.builder.ret_void()
            else:
                generator.llvmir.builder.ret(get_llvmlite_type(fn.value_type)(ir.Undefined))
            
            fn.llvm_irs = str(generator.llvmir.function).splitlines()
            do_reindexing = False
            if (do_reindexing):
                fn.llvm_irs = convert_to_clang_irs(fn.llvm_irs)

            gen_node = generator.symbol_table[function_name]

            generator.function = None

        elif (node.data == "parameter_list"):
            # parameter_list:  parameter_declaration 
            #   |  parameter_list "," parameter_declaration
            # Remove extra nesting so the parameter list location is
            # deterministic at parameter collection time in the function
            # wrap-up
            
            if (len(node.children) > 1):
                gen_node = generate_ir(generator, node.children[0])
                gen_node.append(generate_ir(generator, node.children[2]))
            else:
                gen_node = [generate_ir(generator, node.children[0])]

        elif (node.data == "parameter_declaration"):
            # parameter_declaration:  declaration_specifiers declarator
            # |  declaration_specifiers abstract_declarator?
            
            gen_node = []
            for child in node.children:
                gen_node.append(generate_ir(generator, child))

            parameter_name = None
            parameter_type = gen_node[0]
            if (len(gen_node) > 1):
                parameter_name = gen_node[1].value
            
            # Don't put the parameters in the symbol table yet, since this could
            # be a forward declaration that should not put them because we don't
            # want to modify the overflow with forward declarations, and we may
            # note even get parameter with names here
            parameter = Struct(
                type="parameter", 
                name=parameter_name, 
                value_type=parameter_type, 
            )
            
            gen_node = parameter
                            

        elif (node.data == "init_declarator"):
            # declarator contains one identifier and one or none initializers
            # init_declarator:  declarator
            # |  declarator "=" initializer
            identifier = generate_ir(generator, node.children[0])
            #identifier = get_tree_tokens(node.children[0])[0]
            initializer = None
            if (len(node.children) > 1):
                initializer = generate_ir(generator, node.children[2])
            
            gen_node = [identifier, initializer]

        elif (node.data == "declaration"):
            # declaration:  declaration_specifiers init_declarator_list? ";"

            # declaration contains one type and one or more identifiers and or
            # initializerss

            if (len(generator.symbol_table) == 1):
                # Global scope declaration
                
                # XXX Only forward function declarations are supported, global
                #     variables are not
                assert (
                    (len(node.children) > 1) and 
                    (get_grandson(node, [1, 0, 0, 0, 1]).value == "(")
                ), "Only function forward declarations supported!!!"
                
                function_type = generate_ir(generator, node.children[0])
                # Gather the function parameters
                # XXX This has some unnecessary nesting, find the culprit and
                #     flatten it?
                gen_node = generate_ir(generator, node.children[1])
                function_name = gen_node[0][0][0].value
                # LLVM only allows declaring functions once, ignore if already
                # declared
                # XXX Should this check it matches the existing declaration?
                if (function_name not in generator.symbol_table):
                    parameters = []
                    if (gen_node[0][0][2] != ")"):
                        parameters = gen_node[0][0][2]
                    fn = create_function(function_name, function_type, parameters)
                    generator.symbol_table[function_name] = fn

            else:
                # Local scope variable, stored in the stack
                
                # The declarator list may contain initializer so it needs
                # generating
                if (len(node.children) > 1):
                    gen_node = generate_ir(generator, node.children[1])
                    
                # Register the variable and create an IR node to hold the
                # initializer, if any
                decl_type = generate_ir(generator, node.children[0])
                for identifier, initializer in gen_node:
                    variable = Struct(
                        type="variable", 
                        name=identifier.value, 
                        value_type=decl_type,
                        # Value_reg and value_ref will be assigned on usage
                    )
                    generator.symbol_table[identifier.value] = variable
                    
                    if (initializer is not None):
                        # XXX This should come from gen_node instead of having
                        #     to recreate it here?
                        a = Struct(type="identifier", value=identifier.value)
                        b = initializer

                        generate_assign_ir(generator, a, b)
                        
            gen_node = Struct(type="ir", value_type="void", ir_reg=None)
            

        elif (node.data == "block_item_list"):
            # block_item_list:  block_item
            # |  block_item_list block_item

            # The code is accumulated in the builder, just return the last block
            # in the list
            gen_node = generate_ir(generator, node.children[0])
            if (len(node.children) > 1):
                gen_node = generate_ir(generator, node.children[1])
                
            
        elif (node.data == "integer_constant"):
            # XXX Check non decimal encoding (hex, oct)
            value = node.children[0]
            value_type = "int"
            # Conservative suffix, may be smaller
            suffix = value[-3:].upper()
            value_len = len(value)
            # Check type suffixes
            if ("LL" in suffix):
                value_type = "long long"
                value_len -= 2
            elif ("L" in suffix):
                # Note LL was checked before, so checking one L here is safe
                value_type = "long"
                value_len -= 1
            # Check sign suffix
            if ("U" in suffix):
                value_type = "unsigned " + value_type
                value_len -= 1
            value = int(value[:value_len])
            
            gen_node = Struct(type="constant", value_type=value_type, value=value)

        elif (node.data == "floating_constant"):
            float_type = "double"
            value = node.children[0].value
            if (value[-1] in ["f", "F"]):
                float_type = "float"

            if (value[-1] in ["f", "F", "L", "l"]):
                value = value[:-1]

            value = float(value)

            gen_node = Struct(type="constant", value_type = float_type, value= value)

        elif (node.data == "identifier"):
            gen_node = Struct(type="identifier", value=node.children[0].value)

        elif ((node.data == "type_name") or (node.data == "declaration_specifiers")):
            # type_name:  specifier_qualifier_list abstract_declarator?
            # declaration_specifiers:  storage_class_specifier declaration_specifiers?
            #   |  type_specifier declaration_specifiers?
            #   |  type_qualifier declaration_specifiers?
            #   |  function_specifier declaration_specifiers?

            # XXX Right now assume there's only one type and it's one of the
            #     basic types, once complex types are supported, the types will
            #     have to be put in the symbol table and parsed properly
            res_type = get_tree_tokens(node)
            res_type = string.join(res_type, " ")
            gen_node = res_type

        elif (node.data == "iteration_statement"):
            # iteration_statement:  "while" "(" expression ")" statement
            # |  "do" statement "while" "(" expression ")" ";"
            # |  "for" "(" expression? ";" expression? ";" expression? ")" statement
            # |  "for" "(" declaration expression? ";" expression? ")" statement
            if (node.children[0] == "while"):
                # iteration_statement:  "while" "(" expression ")" statement

                builder = generator.llvmir.builder
                loop_cond_bb = builder.function.append_basic_block("whilecond")
                loop_end_bb = builder.function.append_basic_block("whileend")
                loop_body_bb = builder.function.append_basic_block("whilebody")

                # Save old break/continue, set new
                prev_break_bb = generator.llvmir.break_bb
                prev_continue_bb = generator.llvmir.continue_bb 
                generator.llvmir.break_bb = loop_end_bb
                generator.llvmir.continue_bb = loop_cond_bb

                # Jump to the condition
                generate_branch_ir(loop_cond_bb)
                
                # Generate loop condition
                generator.llvmir.builder.position_at_start(loop_cond_bb)
                gen_node = generate_ir(generator, node.children[2])
                # Convert expression to _Bool
                a_ir_reg, a_type = get_ir_reg_and_type(gen_node)
                res_type = "_Bool"
                if (a_type != res_type):
                    a_ir_reg = generate_extern_call_ir(generator, 
                        get_fn_name("cnv", res_type, a_type), res_type, [a_type, a_ir_reg])

                # Jump to exit or to start of loop
                generate_cbranch_ir(a_ir_reg, loop_body_bb, loop_end_bb)

                # Generate loop body
                generator.llvmir.builder.position_at_start(loop_body_bb)
                generate_ir(generator, node.children[4])
                generate_branch_ir(loop_cond_bb)

                # Restore old break/continue
                generator.llvmir.break_bb = prev_break_bb
                generator.llvmir.continue_bb = prev_continue_bb

                # Generate the end
                generator.llvmir.builder.position_at_start(loop_end_bb)

            elif (node.children[0] == "do"):
                # |  "do" statement "while" "(" expression ")" ";"

                builder = generator.llvmir.builder
                loop_cond_bb = builder.function.append_basic_block("docond")
                loop_body_bb = builder.function.append_basic_block("dobody")
                loop_end_bb = builder.function.append_basic_block("doend")

                # Save old break/continue, set new
                prev_break_bb = generator.llvmir.break_bb
                prev_continue_bb = generator.llvmir.continue_bb 
                generator.llvmir.break_bb = loop_end_bb
                generator.llvmir.continue_bb = loop_cond_bb

                # Jump to the loop body
                generate_branch_ir(loop_body_bb)

                # Generate loop body
                generator.llvmir.builder.position_at_start(loop_body_bb)
                generate_ir(generator, node.children[1])
                generate_branch_ir(loop_cond_bb)

                # Generate loop condition
                generator.llvmir.builder.position_at_start(loop_cond_bb)
                gen_node = generate_ir(generator, node.children[4])
                # Convert expression to _Bool
                a_ir_reg, a_type = get_ir_reg_and_type(gen_node)
                res_type = "_Bool"
                if (a_type != res_type):
                    a_ir_reg = generate_extern_call_ir(generator, 
                        get_fn_name("cnv", res_type, a_type), res_type, [a_type, a_ir_reg])

                # Jump to exit or to start of loop
                generate_cbranch_ir(a_ir_reg, loop_body_bb, loop_end_bb)

                # Restore old break/continue
                generator.llvmir.break_bb = prev_break_bb
                generator.llvmir.continue_bb = prev_continue_bb

                # Generate the end
                generator.llvmir.builder.position_at_start(loop_end_bb)

            elif (node.children[0] == "for"):
                # |  "for" "(" expression? ";" expression? ";" expression? ")" statement
                # |  "for" "(" declaration expression? ";" expression? ")" statement

                # The foor loop creates a scope for the declaration that sits
                # between the parent scope and the body scope, those
                # declarations can hide and be hidden, but there's no collision
                # with one or the other
                generator.symbol_table.push_scope()

                # Skip over for and (
                next_child = 2

                # Generate the loop setup
                if (node.children[next_child] != ";"):
                    # Declaration or expression, note declaration includes the
                    # initializer and ";" already
                    gen_node = generate_ir(generator, node.children[next_child])
                    
                    if (node.children[next_child].data == "expression"):
                        next_child += 1

                # Skip over ;
                next_child += 1
                    
                generator.symbol_table.push_scope()
                
                builder = generator.llvmir.builder

                loop_cond_bb = builder.function.append_basic_block("forcond")
                loop_incr_bb = builder.function.append_basic_block("forincr")
                loop_body_bb = builder.function.append_basic_block("forbody")
                loop_end_bb = builder.function.append_basic_block("forend")

                # Save old break / continue, set new
                prev_break_bb = generator.llvmir.break_bb
                prev_continue_bb = generator.llvmir.continue_bb 
                generator.llvmir.break_bb = loop_end_bb
                generator.llvmir.continue_bb = loop_incr_bb

                generate_branch_ir(loop_cond_bb)
                
                generator.llvmir.builder.position_at_start(loop_cond_bb)
                if (node.children[next_child] != ";"):
                    # Generate the loop condition
                    
                    gen_node = generate_ir(generator, node.children[next_child])

                    # Convert expression to _Bool
                    a_ir_reg, a_type = get_ir_reg_and_type(gen_node)
                    res_type = "_Bool"
                    if (a_type != res_type):
                        a_ir_reg = generate_extern_call_ir(generator, 
                            get_fn_name("cnv", res_type, a_type), res_type, [a_type, a_ir_reg])

                    # Jump to exit or to start of loop
                    generate_cbranch_ir(a_ir_reg, loop_body_bb, loop_end_bb)
                    next_child += 1

                else:
                    generate_branch_ir(loop_body_bb)
                
                # Skip over ;
                next_child += 1

                generator.llvmir.builder.position_at_start(loop_incr_bb)
                if (node.children[next_child] != ")"):
                    # Generate the loop increment
                    gen_node = generate_ir(generator, node.children[next_child])

                    next_child += 1
                generate_branch_ir(loop_cond_bb)

                next_child += 1

                # Generate the for body 
                generator.llvmir.builder.position_at_start(loop_body_bb)
                generate_ir(generator, node.children[next_child])
                generate_branch_ir(loop_incr_bb)
                
                # Generate the end
                generator.llvmir.builder.position_at_start(loop_end_bb)

                # Restore old break/continue
                generator.llvmir.break_bb = prev_break_bb
                generator.llvmir.continue_bb = prev_continue_bb

                # for body
                generator.symbol_table.pop_scope()
                # for declaration
                generator.symbol_table.pop_scope()

            else:
                assert False, "Unsupported iteration statement %s" % node

        elif (node.data == "selection_statement"):
            # selection_statement:  "if" "(" expression ")" statement
            # |  "if" "(" expression ")" statement "else" statement
            # |  "switch" "(" expression ")" statement
            if (node.children[0] == "if"):
                # Generate the condition expression
                gen_node = generate_ir(generator, node.children[2])
                
                a_ir_reg, a_type = get_ir_reg_and_type(gen_node)
                
                # Convert expression to _Bool
                res_type = "_Bool"
                if (a_type != res_type):
                    a_ir_reg = generate_extern_call_ir(generator, 
                        get_fn_name("cnv", res_type, a_type), res_type, [a_type, a_ir_reg])
                
                builder = generator.llvmir.builder
                if_then_bb = builder.function.append_basic_block("ifthen")
                if_else_bb = builder.function.append_basic_block("ifelse")
                if_end_bb = builder.function.append_basic_block("ifend")
                
                generate_cbranch_ir(a_ir_reg, if_then_bb, if_else_bb)

                # Generate then
                generator.llvmir.builder.position_at_start(if_then_bb)
                generate_ir(generator, node.children[4])
                generate_branch_ir(if_end_bb)
                
                generator.llvmir.builder.position_at_start(if_else_bb)
                if (len(node.children) > 5):
                    # Generate else
                    generate_ir(generator, node.children[6])
                generate_branch_ir(if_end_bb)

                generator.llvmir.builder.position_at_start(if_end_bb)                    

                # XXX Missing gen node
            else:
                assert False, "Unhandled switch statement"


        elif (len(node.children) == 1):
            # Unify with the n children below?
            # XXX This catch all should go at some point?
            # XXX This is cumbersome because sometimes we want to always receive
            #     a list to unify handling, on the other hand creating a one 
            #     element list here would do nesting, but it's also convenient
            # XXX Ideally we should handle all nodes individually without catch-all
            gen_node = generate_ir(generator, node.children[0])
            
        else:
            gen_node = []
            for child in node.children:
                gen_node.append(generate_ir(generator, child))
        
    elif (isinstance(node, lark.Token)):
        gen_node = node.value

    else:
        assert False, "Unexpected node type %s" % node
    
    return gen_node


llvm_initialized = False

def llvm_compile(llvm_ir, function_signatures):
    global llvm_initialized
    if (not llvm_initialized):
        # This switches the assembler emit from at&t to intel, needs to be done
        # before initializing llvmlite, otherwise it's ignored
        # Will also give a warning
        #       "for the -x86-asm-syntax option: may only occur zero or one times!"
        # when llvm_compile is initialized more than once (but other than that
        # multiple initialization doesn't seem to be a problem)
    
        # XXX This probably doesn't affect the input assembler, only the output, which
        #     has to be done in AT&T eg
        #       call void asm sideeffect "movl %eax, %eax", "~{dirflag},~{fpsr},~{flags}"() #2


        llvm.set_option('', '--x86-asm-syntax=intel')

        # All these initializations are required for code generation
        
        llvm.initialize()
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()  # yes, even this one

        llvm_initialized = True

        # XXX Reuse some of the objects created below across llvm_compile 
        #     invocations?

    def create_target_machine():
        # Create a target machine representing the host
        target = llvm.Target.from_default_triple()
        target_machine = target.create_target_machine()

        return target_machine

    def create_execution_engine(target_machine):
        """
        Create an ExecutionEngine suitable for JIT code generation on
        the host CPU.  The engine is reusable for an arbitrary number of
        modules.
        """
        # And an execution engine with an empty backing module
        backing_mod = llvm.parse_assembly("")
        engine = llvm.create_mcjit_compiler(backing_mod, target_machine)
        return engine


    def compile_ir(engine, llvm_ir):
        """
        Compile the LLVM IR string with the given engine.
        The compiled module object is returned.
        """
        # Create a LLVM module object from the IR
        mod = llvm.parse_assembly(llvm_ir)
        mod.verify()
        # Now add the module and make sure it is ready for execution
        engine.add_module(mod)
        engine.finalize_object()
        engine.run_static_constructors()
        
        return mod

    def create_pass_manager_builder(opt=2, loop_vectorize=False,
                                    slp_vectorize=False):
        # See https://github.com/numba/llvmlite/blob/master/llvmlite/llvmpy/passes.py
        def _inlining_threshold(optlevel, sizelevel=0):
            # Refer http://llvm.org/docs/doxygen/html/InlineSimple_8cpp_source.html
            if optlevel > 2:
                return 275

            # -Os
            if sizelevel == 1:
                return 75

            # -Oz
            if sizelevel == 2:
                return 25

            return 225

        pmb = llvm.create_pass_manager_builder()
        pmb.opt_level = opt
        # XXX Investigate enabling these. At least not disabling loop-vectorize
        #     in clang command line is known to produce different code for the
        #     ffact functions.c test
        pmb.loop_vectorize = loop_vectorize
        pmb.slp_vectorize = slp_vectorize
        # XXX An inlining threshold of 20 is enough to inline the utility
        #     functions and generates closer code to clang -O2 in one single
        #     case fsum_indirect2, since otherwise epycc does one final call
        #     recursion elimintion that clang doesn't do
        pmb.inlining_threshold = _inlining_threshold(opt)

        return pmb

    jit_lib = Struct(ir = llvm_ir)

    target_machine = create_target_machine()
    engine = create_execution_engine(target_machine)
    mod = compile_ir(engine, llvm_ir)


    # XXX All the attributes should probably go under some safe prefix to
    #     prevent from colliding with the user-defined functions that are being
    #     compiled or under the function name, but most of them are for the
    #     whole jit_lib, not per function
    

    # XXX Need to keep some of these around to prevent access violations when
    #     calling the function right after leaving this function, presumably
    #     because of garbage collection, find out which ones
    
    jit_lib.mod = mod
    jit_lib.tm = target_machine
    jit_lib.engine = engine
    jit_lib.asm = target_machine.emit_assembly(mod)
    jit_lib.ir = str(mod)

    # Dot generation is known to take half the test running time under
    # runsnakerun, disable it unless debug 
    # XXX Get these from configs and/or expose a generate_dot
    # function
    debug = (__name__ == "__main__")
    output_dot = debug
    output_optimized_dot = debug
    if (output_dot):
        for function_signature in function_signatures:
            func = mod.get_function(function_signature.name)
            dot = llvm.get_function_cfg(func, show_inst=True)
            dot_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out", function_signature.name + ".dot")
            with open(dot_filepath, "w") as f:
                f.write(dot)
            invoke_dot(dot_filepath)
    
    
    # Optimize the module
    pmb = create_pass_manager_builder()
    pm = llvm.create_module_pass_manager()
    pmb.populate(pm)
    pm.run(mod)

    jit_lib.ir_optimized = str(mod)
    jit_lib.asm_optimized = target_machine.emit_assembly(mod)
    
    for function_signature in function_signatures:

        # Look up the function pointer (a Python int)
        func_ptr = engine.get_function_address(function_signature.name)

        if (output_optimized_dot):
            func = mod.get_function(function_signature.name)
            dot = llvm.get_function_cfg(func, show_inst=True)
            dot_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out", function_signature.name + ".optimized.dot")
            with open(dot_filepath, "w") as f:
                f.write(dot)
            invoke_dot(dot_filepath)

        # Run the function via ctypes
        cfunc = ctypes.CFUNCTYPE(*function_signature.ctypes)(func_ptr)
        setattr(jit_lib, function_signature.name, cfunc)

    # XXX Missing publishing the globals once there's global support
        
    return jit_lib


def load_functions_ir(ir_filepath):
    ir_functions = {}
    with open(ir_filepath, "r") as f:
        ir_function = None
        for l in f:
            # define signext i8 @add__char__char__char(i8 signext, i8 signext) #0 {
            l = l.strip()
            if (l.endswith("{")):
                ir_function = []
                m = re.search("@([^(]+)", l)
                function_name = m.group(1)

            if (ir_function is not None):
                ir_function.append(l)

            # Note clang puts some debug lines that end in }, only deal with }
            # if we are inside a function
            if ((ir_function is not None) and (l.endswith("}"))):
                ir_functions[function_name] = ir_function
                function_name = None
                ir_function = None
    return ir_functions


def epycc_generate(source, debug = False):
    # XXX check if we can tag which tokens to keep with "!" in the rule instead 
    #     of keep_all_tokens

    # Use the standard lexer since the earley parser dynamic lexer confuses return
    # token with an identifier when a parenthesis follows, eg
    #   float fdouble(float a) {
    #     return (a * 2.0 + 3.0) * 6.0 ;
    #   }
    # Another way of fixing it is by adding a blacklist of keywords to the identifier
    # terminal:
    #   IDENTIFIER: /(?!(break|return)\b)/CNAME
    # but the negative lookahead is probably less efficient, cumbersome to add all
    # the terminals that may hit that problem, and the grammar doesn't seem to need
    # the dynamic lexer anyway

    # XXX Earley properly auto resolves ambiguity between identifier and
    #    typedef_name that the LALR(1) chokes on, but it doesn't detect the "T* t;"
    #    ambiguity (this can be seen when opened with ambiguity="explicit", there
    #    are no trees due to T* t; under _ambig node), so still requires some 
    #    lexer hack or such

    # XXX Earley is really slow (~13 lines per second) and causes stack overflow
    #     with long C files (thousands of lines). Massage the grammar to be
    #     LALR(1) and switch to LALR parser. Optionally once epycc can
    #     bootstrap itself and write the parser code code in C (the AST
    #     traversal can remain in Python).
    grammar_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 
        "grammars", "c99_phrase_structure_grammar.lark")
    parser = lark.Lark.open(grammar_filepath, keep_all_tokens="True", 
        lexer="standard")
    tree = parser.parse(source)

    if (debug):
        print tree.pretty()

    generator = Struct(
        symbol_table = SymbolTable(), 
        llvmir = Struct(module=ir.Module(), break_bb = None, continue_bb = None, function=None, externs=dict())
    )
    
    generate_ir(generator, tree)

    epycc_dirpath = os.path.dirname(os.path.abspath(__file__))
    generated_c_filepath = os.path.join(epycc_dirpath, "generated", "irs.c")
    generated_ir_filepath = os.path.join(epycc_dirpath, "generated", "irs.ll")

    # Regenerate if they don't exist or same or older than this python file
    if (not os.path.exists(generated_ir_filepath) or
        (os.path.getmtime(generated_ir_filepath) <= os.path.getmtime(__file__))):
        precompile_c_snippets(generated_c_filepath, generated_ir_filepath)
    
    all_externs = load_functions_ir(generated_ir_filepath)
    
    llvm_irs = []
    function_signatures = []
    function_externs = generator.llvmir.externs.keys()
    assert len(generator.symbol_table) == 1, "Symbol table is not at global scope!!!"
    # Collect function signatures in ctypes format
    for sym in generator.symbol_table.values():
        if (sym.type == "function"):

            llvm_irs.extend(sym.llvm_irs)
            llvm_irs.append("")
            llvm_irs.append("")

            function_signature = Struct(
                name=sym.name, 
                ctypes = [get_ctype(sym.value_type)] + 
                    [get_ctype(parameter.value_type) for parameter in sym.parameters]
            )

            function_signatures.append(function_signature)

    
    for function_extern in function_externs:
        # Dump the extern functions needed by this module
        extern = all_externs[function_extern]
        llvm_irs.append(extern[0])
        for l in extern[1:-1]:
            llvm_irs.append("  " + l)
        llvm_irs.append(extern[-1])
        llvm_irs.append("")

    llvm_ir = string.join(llvm_irs, "\n")

    return llvm_ir, function_signatures


def epycc_compile(source, debug = False):
    # XXX This does reinitialization when called multiple times and causes 
    #     warnings like 
    #       :for the -x86-asm-syntax option: may only occur zero or one times!
    #     Do proper tear down or return some kind of singleton

    llvm_ir, function_signatures = epycc_generate(source, debug)
    lib = llvm_compile(llvm_ir, function_signatures)

    return lib



def llvm_ir_diff(filepath_a, filepath_b, function_names = None):
    """
    IR file differ. Tries to find a remapping of namedvalues between a and b 
    and reordering of phi instructions and reshuffling of phi parameters to
    prevent false mismatches.

    Starts with the initial blocks and works its way from there.


    This uses llvm.bindings assembler parsing functionality, but note that
    default names for namedvalues (eg coming from clang) don't appear as .name
    attribute, eg the parameters and destination and operand registers have an
    empty .name. For non-default names (Eg coming from epycc) they do appear.

    str(instruction) or str(operand) does show the namedvalue.

    XXX Maybe a solution is manually convert all %n default names to "%n" before
        or after loading.


    It also does phi attribute shuffling to prevent mismatches due to different
    ordering between a and b.
    

    Also does phi instruction reordering to cater for the following false
    mismatch in this case from ffor_nested

        ; <label>:4:                                      ; preds = %27, %2
        %5 = phi i32 [ %34, %27 ], [ -4, %2 ]
        %6 = phi i32 [ %33, %27 ], [ -1, %2 ]
        %7 = phi i32 [ %32, %27 ], [ 0, %2 ]
        %8 = phi i32 [ %30, %27 ], [ 0, %2 ]
        %9 = phi i32 [ %29, %27 ], [ 0, %2 ]
        %10 = lshr i32 %6, 3
        %11 = icmp eq i32 %8, 0
        br i1 %11, label %27, label %12

         vs. 

        forcond.1.preheader:                              ; preds = %forend.1, %entry
        %indvars.iv10 = phi i32 [ %indvars.iv.next11, %forend.1 ], [ -4, %entry ]
        %indvars.iv8 = phi i32 [ %indvars.iv.next9, %forend.1 ], [ -1, %entry ]
        %indvars.iv = phi i32 [ %indvars.iv.next, %forend.1 ], [ 0, %entry ]
        %.4.05 = phi i32 [ %13, %forend.1 ], [ 0, %entry ]
        %.7.03 = phi i32 [ %14, %forend.1 ], [ 0, %entry ]
        %1 = lshr i32 %indvars.iv8, 3
        %2 = icmp eq i32 %.7.03, 0
        br i1 %2, label %forend.1, label %forbody.1.preheader

        those two snippets are equivalent but the phi instructions are sorted 
        differently because they are independent, specifically 

            %8 = phi i32 [ %30, %27 ], [ 0, %2 ]
            %9 = phi i32 [ %29, %27 ], [ 0, %2 ]

        and 
            %.4.05 = phi i32 [ %13, %forend.1 ], [ 0, %entry ]
            %.7.03 = phi i32 [ %14, %forend.1 ], [ 0, %entry ]


        Looks like the phi instructions can be sorted by looking at the source
        value of the value,label pair. The epycc generated doesn't have a 
        monotonically increasing value to sort by (although this is irrelevant, 
        you could also sort both clang and epycc phi instructions by looking
        at the , and could use the remapping
        value instead, but the remapping must not come from a phi node or
        it could have been remapped wrongly already.

        XXX The bad option is to try all register naming combinations or instruction
            orderings.

        XXX Looks like this sorting could be applied to any instruction, disregarding
            the data hazards between instructions since we only care about finding
            a mapping between both sets of instructions.

        XXX Should the reordering care about data hazards? Yes in the general
            case not when comparing against default naming file? (because default
            naming guarantees montonically increasing registers so any remapping 
            would also guarantee it?)
            
    """

    def search_block(block_str, blocks):
        block = block_str_to_block.get(block_str, None)
        if (block is None):
            for block in blocks:
                if (block_str == str(block)):
                    block_str_to_block[block_str] = block
                    break
                
        return block
        
    def sort_phi_operands(tokens, remap_sort, remap_result):
        # XXX This accesses the remapping table, should be passed as param?
        phi_operands = [ tokens[4+i*4:4+(i+1)*4] for i in (xrange((len(tokens) - 4) / 4)) ]
        if (remap_sort):
            phi_operands = sorted(phi_operands, key= lambda a: [remapping_table[i] for i in a])
        else:
            phi_operands = sorted(phi_operands)

        if (remap_result):
            phi_operands = [remapping_table[item] for sublist in phi_operands for item in sublist]

        else:
            phi_operands = [item for sublist in phi_operands for item in sublist]

        return phi_operands
        
    def sort_instructions(instructions, remapping_table=None):

        def cmp_instructions(i0, i1):
            """
            Compare two (index, instruction) pairs in order to be sorted.

            Normal instructions will be first in the list, then remapped phis,
            then unremapped phis. If remap is False then all instructions are
            considered remapped.

            Right now this does special casing for phi instructions, the other
            instructions are compared verbatim.
            """
            remap = (remapping_table is not None)

            i0_i, instr0 = i0
            i1_i, instr1 = i1
            str_instr0 = str(instr0).strip()
            str_instr1 = str(instr1).strip()
            tokens0 = re.split(r"[ ,]+", str_instr0)
            tokens1 = re.split(r"[ ,]+", str_instr1)
            
            res = 0
            
            # Sort normal instructions first, then remapped phis, then
            # unremapped phis (note in all a instructions are considered
            # remapped)
            if ((instr0.opcode == "phi") and (instr1.opcode == "phi")):
                lacks_remappings0 = lacks_remappings1 = False
                if (remap):
                    lacks_remappings0 = any([token not in remapping_table for token in tokens0])
                    lacks_remappings1 = any([token not in remapping_table for token in tokens1])

                if (lacks_remappings0 and lacks_remappings1):
                    # When both have remappings missing, sort by original order
                    res = cmp(i0_i, i1_i)
                
                elif (lacks_remappings0):
                    # Instruction missing remappings to the end
                    res = 1
                
                elif (lacks_remappings1):
                    # Instruction missing remappings to the end
                    res = -1

                else:
                    
                    # Order depending on alphabetically sorted operands 
                    # XXX This could also check the return type and/or the
                    #     destination register
                    phi_operands0 = sort_phi_operands(tokens0, remap, remap)
                    phi_operands1 = sort_phi_operands(tokens1, remap, remap)

                    res = cmp(phi_operands0, phi_operands1)
                    # print "comparing", phi_operands0, "vs", phi_operands1
                    assert(res != 0)
                    
            elif (instr0.opcode == "phi"):
                res = 1

            elif (instr1.opcode == "phi"):
                res = -1

            else:
                # sort by original position
                res = cmp(i0_i, i1_i)             

            return res

        index_instructions = [ (i, item) for i, item in enumerate(instructions)]
        instructions_sorted = [item for i, item in sorted(index_instructions, cmp=cmp_instructions)]
        
        return instructions_sorted


    mismatch_count = 0
    # tuples of a,b instructions mismatching, indexed by function name
    mismatches = {}

    with open(filepath_a, "r") as f:
        llvm_ir_a = f.read()

    with open(filepath_b, "r") as f:
        llvm_ir_b = f.read()

    mod_a = llvm.parse_assembly(llvm_ir_a)
    mod_b = llvm.parse_assembly(llvm_ir_b)

    fns_a = {}
    fns_b = {}
    
    if (function_names is not None):
        if (isinstance(function_names, str)):
            function_names = [function_names]
        function_names = set(function_names)

    for fn_a in mod_a.functions:
        if ((function_names is not None) and (fn_a.name not in function_names)):
            continue

        # look for the function in b, note it's intentional this will ignore
        # and not return as diffs the functions in b not present in a
        for fn_b in mod_b.functions:
            if (fn_a.name == fn_b.name):
                break
        else:
            # fn_a doesn't exist in b, add each instruction to the mismatch
            # XXX Note this will contain comments and the function header, which
            #     won't appear on a regular per block diff where both a and
            #     bhave the function
            mismatches[fn_a.name] = [instr for instr in str(fn_a).splitlines()]
            continue
            
        function_mismatch_count = 0
        mismatches[fn_a.name] = []

        # Get the entry blocks
        block_a = list(fn_a.blocks)[0]
        block_b = list(fn_b.blocks)[0]

        # XXX Should this abort if the number of arguments or the return type 
        #     is already different?
        assert(len(list(fn_a.arguments)) == len(list(fn_b.arguments)))
        
        # Add the function arguments to the remapping table
        remapping_table = {
            "%%%s" % argument_b.name if (argument_b.name != "") else "%%%d" % i :
            "%%%s" % argument_a.name if (argument_a.name != "") else "%%%d" % i 
                for i, (argument_a, argument_b) in enumerate(zip(fn_a.arguments, fn_b.arguments))
        }
        
        # Add the initial block to the remapping table, this may appear in 
        # labels but not in a label declaration if the IR uses default naming
        block_name_a = block_a.name if (block_a.name != "") else "%%%d" % len(list(fn_a.arguments))
        block_name_b = block_b.name if (block_b.name != "") else "%%%d" % len(list(fn_a.arguments))
        remapping_table["%%%s" % block_name_b] = block_name_a

        # Block cache for searches
        block_str_to_block = dict()

        pending_block_pairs_queue = [(block_a, block_b)]
        done_block_pairs = set()
        while (len(pending_block_pairs_queue) > 0):
            block_pair = pending_block_pairs_queue.pop(0)
            done_block_pairs.add(block_pair)
            block_a, block_b = block_pair
            instructions_a = block_a.instructions
            instructions_b = block_b.instructions

            # Create a list to be sorted with [(index, instruction_a, instruction_b), ...] 
            # Reorder the phi instructions wrt to a sort func
            
            instructions_sorted_a = sort_instructions(instructions_a)
            instructions_sorted_b = sort_instructions(instructions_b, remapping_table)

            debug_instruction_sorting = False
            if (debug_instruction_sorting):
                print "a sorted\n", string.join([str(instr) for instr in instructions_sorted_a], "\n")
                print "b sorted\n", string.join([str(instr) for instr in instructions_sorted_b], "\n")

            needs_revisiting = False
            # Fill with empty strings so they are detected as mismatches
            # Note this works because it gets handled as different token lengths
            delta_len_a_b = len(instructions_sorted_a) - len(instructions_sorted_b)
            instructions_sorted_b.extend([""] * (max(delta_len_a_b, 0)))
            instructions_sorted_a.extend([""] * (max(-delta_len_a_b, 0)))
            for instr_a, instr_b in zip(instructions_sorted_a, instructions_sorted_b):
                str_instr_a = str(instr_a).strip()
                str_instr_b = str(instr_b).strip()
                # Note some operations (eg switch) include carriage returns,
                # remove those too
                tokens_a = re.split(r"[ ,\n]+", str_instr_a)
                tokens_b = re.split(r"[ ,\n]+", str_instr_b)

                if ((len(tokens_a) != len(tokens_b)) or 
                    (instr_a.opcode != instr_b.opcode) or 
                    ((instr_a.opcode != "phi") and any([token_a != token_b 
                        for token_a, token_b in zip(tokens_a, tokens_b) if not token_b.startswith("%")]))):
                    function_mismatch_count += 1
                    mismatches[fn_a.name].append((str_instr_a, str_instr_b))
                    continue

                remapping_table.update({token_b : token_b for token_b in tokens_b if token_b[0] != "%"})

                # Phi instructions 
                #   %indvars.iv10 = phi i32 [ %indvars.iv.next11, %forend.1 ], [ -4, %entry ]
                # select over depending on where it came from
                # the selection options can be randomly sorted, so we need
                # to ensure they are compared properly.
                
                # If it's a phi node, reorder the options tokens
                # alphabetically wrt the remapping, this requires the
                # remapping to be available for those options

                # ['%10', '=', 'phi', 'i32', '[', '%5', '%4', ']', '[', '%8', '%7', ']']
                # ['%merge', '=', 'phi', 'i32', '[', '%2', '%dobody.endif', ']', '[', '%.4.0', '%dobody', ']']
                if (instr_a.opcode == "phi"):
                    # If not all the tokens are in the remapping table, put
                    # the block back in the queue to revisit later
                    
                    # XXX This needs to check for infinite loops?
                    if (any([token_b not in remapping_table for token_b in tokens_b])):
                        needs_revisiting = True
                        continue

                    else:
                        phi_operands_a = sort_phi_operands(tokens_a, False, False)
                        phi_operands_b = sort_phi_operands(tokens_b, True, False)

                        tokens_a = tokens_a[:4] + phi_operands_a
                        tokens_b = tokens_b[:4] + phi_operands_b

                mismatch_found = False
                for token_a, token_b in zip(tokens_a, tokens_b):
                    if (token_b in remapping_table):
                        if (remapping_table[token_b] != token_a):
                            mismatch_found = True
                            break
                        
                    else:
                        remapping_table[token_b] = token_a

                if (mismatch_found):
                    # There was a mismatch, done with this instruction
                    function_mismatch_count += 1
                    mismatches[fn_a.name].append((str(instr_a), str(instr_b)))

                    continue

                # Find other blocks to traverse by pushing to the queue operands
                # of type "label"
                for operand_a, operand_b in zip(instr_a.operands, instr_b.operands):
                    # instr.opcode is a string with the opcode, but has information
                    # missing, eg "icmp" for "icmp gte" 

                    # str(operand) returns type and name, but name is empty for auto-gen
                    # for labels, the str() gives the full basic block the label 
                    # points to

                    if (str(operand_a.type) == "label"):
                        # Find the block by string search
                        next_block_a = search_block(str(operand_a), fn_a.blocks)
                        next_block_b = search_block(str(operand_b), fn_b.blocks)
                        
                        assert(next_block_a is not None)
                        assert(next_block_b is not None)
                        
                        next_block_pair = (next_block_a, next_block_b)
                        if (next_block_pair not in done_block_pairs):
                            pending_block_pairs_queue.append(next_block_pair)

            # Re-enqueue if this block needs revisiting, but don't do it if
            # there are mismatches since it could prevent completing the
            # remapping table and loop forever
            if (needs_revisiting and (function_mismatch_count == 0)):
                # XXX This could remove the completely remapped instructions
                #     from the block
                done_block_pairs.remove(block_pair)
                pending_block_pairs_queue.append(block_pair)

        mismatch_count += function_mismatch_count

    return mismatches


if (__name__ == "__main__"):

    # print llvm_ir_diff("_out/gold_function.c.optimized.ll", "_out/function.c.optimized.ll", "ffib")

    source = open("samples/current.c").read()
    lib = epycc_compile(source, True)
    print lib.ir
    print lib.ir_optimized
    #print lib.asm
    #print lib.asm_optimized
    print lib.fsum_indirect1(10)

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


    def __getitem__(self, key):
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
    c_to_irtypes = {
        "long double" : "x86_fp80",
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
    assert all((c_type in all_types) for c_type in c_to_irtypes)
    assert all((c_type in c_to_irtypes) for c_type in all_types)
    
    return c_to_irtypes[t]
        
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
    # Generate the precompiled IR in irs.ir
    # clang_filepath = R"C:\android-ndk-r15c\toolchains\llvm\prebuilt\windows-x86_64\bin\clang.exe"
    clang_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out", "clang.exe")
    # For privacy reasons and since some ir files are pushed to the repo, don't
    # leak the local path in the LLVM moduleid comment of 
    cmd = R"%s -mllvm --x86-asm-syntax=intel -S -std=c99 -emit-llvm %s -o %s %s" % (
        clang_filepath,
        options,
        os.path.relpath(ir_filepath),
        os.path.relpath(c_filepath)
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


def generate_ir(generator, node):
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


    def get_ref_reg_and_type(a, irs, externs):
        # XXX This is wrong in the case of lvalues of complex expressions 
        #     (pointers, arrays, structs...)
        
        # Get reg and type to deal with allocations
        get_reg_and_type(a, irs, externs)

        a = generator.symbol_table[a.value]
        assert a.type == "variable"

        return a.value_ref, a.value_reg, a.value_type


    def get_reg_and_type(a, irs, externs):
        """
        Get reg and type for a given source or destination operand

        Generate the necessary instructions and extern references in case a is
        not in a register yet (identifier or constants)
        """

        if (a.type == "identifier"):
            a = generator.symbol_table[a.value]
            a_type = a.value_type
            a_llvm_type = get_llvm_type(a_type)

            # l-values need storage to store the value, allocate it if it doesn't
            # have one already
            if (not hasattr(a, "value_ref")):
                a.value_ref = generator.current_register
                generator.current_register += 1
                # "%3 = alloca i32, align 4"
                irs.append("%%%d = alloca %s, align 4" % (a.value_ref, a_llvm_type))

                # If it has a register it means that it has an initial value, 
                # copy from the register into the storage
                if (hasattr(a, "value_reg")):
                    # "store i32 %0, i32* %3, align 4"
                    irs.append("store %s %%%d, %s* %%%d, align 4" % (a_llvm_type, a.value_reg, a_llvm_type, a.value_ref))

            # Load from the storage to a new register to make sure the register
            # value we use is uptodate
            # XXX This is probably overkill, we should be able to track when the
            #     existing register holding the value is uptodate?
            a.value_reg = generator.current_register
            generator.current_register += 1
            # "%5 = load i32, i32* %4, align 4"
            irs.append("%%%d = load %s, %s* %%%d, align 4" % (a.value_reg, a_llvm_type, a_llvm_type, a.value_ref))
            

            a_reg = a.value_reg
            

        elif (a.type == "constant"):
            # IR can operate on constants directly but allocating a register and
            # storing the constant unifies the path below
            a_type = a.value_type
            a_llvm_type = get_llvm_type(a_type)

            a_reg = generator.current_register
            generator.current_register += 1

            # Allocate a pointer to the data in the stack, store the constant
            # and load into regular (non pointer) register

            # XXX Missing alignment
            # %3 = alloca float, align 4
            irs.append("%%%d = alloca %s, align 4" % (a_reg, a_llvm_type))
                        
            if (a_type in ["float", "double"]):
                # LLVM errors out if the float cannot be converted accurately,
                # always pre-truncate and pass floats in hex, which LLVM
                # requires to be provided in 64-bits even if it's going to a
                # 32-bit float store (but still truncated to a value that will 
                # fit in 32-bits)
                a_value = a.value
                if (a_type == "float"):
                    # Store 64-bit but truncate to 32-bit first to prevent LLVM
                    # erroring out with "floating point constant invalid for
                    # type"
                    a_value = (ctypes.c_float(a.value)).value
                # XXX Review the endianness here?
                a_llvm_value = "0x%016x" % struct.unpack("Q", struct.pack("d", a_value))

            else:
                a_llvm_value = str(a.value)
            
            # store float 2.000000e+00, float* %3, align 4, !dbg !30
            irs.append("store %s %s, %s* %%%d, align 4" % (a_llvm_type, a_llvm_value, a_llvm_type, a_reg))
            a_reg = generator.current_register
            generator.current_register += 1
            # %2 = load double, double* %1, align 4
            irs.append("%%%d = load %s, %s* %%%d, align 4" % (a_reg, a_llvm_type, a_llvm_type, a_reg-1))

        elif (a.type == "ir"):
            a_type = a.value_type
            a_reg = a.value_reg
            irs.extend(a.value)
            externs.update(a.externs)

        else:
            a_type = a.value_type
            a_reg = a.value_reg

        return a_reg, a_type

    def generate_call_ir(generator, irs, externs, fn_name, res_type, type_reg_list):
        """
        Generates IR for the given call and the result type, with arguments type
        and registers in type_reg_list

        Returns the result register
        """
        res_reg = generator.current_register
        generator.current_register += 1

        # %5 = call float @mul__float__float__unsigned_int(float %1, unsigned int %4)
        args = [
            ("%s %%%d" % (get_llvm_type(type_reg_list[i*2]), type_reg_list[i*2+1])) 
                for i in xrange(len(type_reg_list) / 2) 
        ]
        ir = "%%%d = call %s @%s(%s)" % (res_reg, get_llvm_type(res_type), fn_name, string.join(args, ","))
        irs.append(ir)
        externs.add(fn_name)

        return res_reg

    def detach_ir(a, irs, externs):
        if (a.type == "ir"):
            irs.extend(a.value)
            externs.update(a.externs)

            a.value = []
            a.externs = set()

    def generate_assign_ir(generator, a, b):
        irs = []
        externs = set()

        # We need to make sure the IRs for a and b are always generated before
        # the assign or the type conversion, since they could contain stores,
        # etc needed for the conversions
        # XXX This should go away once IR is stored in a global place with a builder
        detach_ir(a, irs, externs)
        detach_ir(b, irs, externs)

        a_ref, a_reg, a_type = get_ref_reg_and_type(a, irs, externs)
        b_reg, b_type = get_reg_and_type(b, irs, externs)

        # Return the value in case it's used as part of an expression
        res_type = a_type
        res_reg = b_reg
        
        # Convert the input type to the result type
        if (b_type != res_type):
            b_reg = generate_call_ir(generator, irs, externs, 
                get_fn_name("cnv", res_type, b_type), res_type, [b_type, b_reg])

        a_llvm_type = get_llvm_type(a_type)
        
        # Perform the store
        # "store i32 %0, i32* %3, align 4"
        irs.append("store %s %%%d, %s* %%%d, align 4" % (a_llvm_type, b_reg, a_llvm_type, a_ref))

        gen_node = Struct(type="ir", value_type=res_type, value_reg = res_reg, value=irs, externs=externs)

        return gen_node


    def generate_binop_ir(generator, a, b, op_sign):
        irs = []
        externs = set()

        # We need to make sure the IRs for a and b are always generated before
        # the assign or the type conversion, since they could contain stores,
        # etc needed for the conversions
        # XXX This should go away once IR is stored in a global place with a builder
        detach_ir(a, irs, externs)
        detach_ir(b, irs, externs)

        # Call the precompiled C function for this expression (these calls
        # are not a performance issue, since they were verified be inlined
        # and optimized in LLVM optimize mode).
        
        # This could be done more elegantly with llvmlite's IR builder but
        # the precompiled function takes care of C-compliant sign
        # extension/truncation of operands and result

        # XXX Investigate using the builder in two steps, first converting
        #     the types, then performing the operation, is very error prone
        #     wrt C compliance
        # XXX Another option is to use the builder and then precompile only
        #     data conversion functions.
        # XXX Yet another option which reduces the number of precompiled
        #     functions is to precompile same-argument operation functions
        #     and then data conversion functions.
        a_reg, a_type = get_reg_and_type(a, irs, externs)
        b_reg, b_type = get_reg_and_type(b, irs, externs)
        res_type = get_result_type(op_sign, a_type, b_type)

        # Convert the input types to the result type
        if (a_type != res_type):
            a_reg = generate_call_ir(generator, irs, externs,
                get_fn_name("cnv", res_type, a_type), res_type, [a_type, a_reg])
            
        if (b_type != res_type):
            b_reg = generate_call_ir(generator, irs, externs, 
                get_fn_name("cnv", res_type, b_type), res_type, [b_type, b_reg])

        # Perform the operation in res_type
        fn_name = get_fn_name(binop_sign_to_name[op_sign], res_type, res_type, res_type)
        res_reg = generate_call_ir(generator, irs, externs, fn_name, 
            res_type, [res_type, a_reg, res_type, b_reg])

        gen_node = Struct(type="ir", value_type=res_type, value_reg = res_reg, value=irs, externs=externs)

        return gen_node


    gen_node = None
    # Node can be Token or Tree
    if (type(node) is lark.Tree):

        #
        # Before children visit actions
        #

        if (node.data == "function_definition"):
            # function_definition
            #   declaration_specifiers
            #     type_specifier	float
            #   declarator
            #     direct_declarator
            #       direct_declarator
            #         identifier	fadd
            # ['float', ['fadd', '(', [['float', 'a'], ',', ['float', 'b']], ')'], ['{', ['return', ['a', '+', 'b'], ';'], '}']]
            function_type, function_name = (
                # XXX This assumes the type is basic type
                # XXX This should be done once declaration_specifiers are read,
                #     but we need the function in the symbol table before the
                #     compound statement is read for recursive functions and
                #     with the type?
                string.join(get_tree_tokens(node.children[0]), " "), 
                get_grandson(node, (1, 0, 0, 0, 0)).value
            )
            generator.symbol_table[function_name] = Struct(type = "function", name=function_name, value_type=function_type)
            
            # Reset the register allocator index on every new function Looks
            # like LLVM IR requires the n function parameters take the first n
            # indices, then there's an empty index, and then the indices for the
            # local variables (index 0 will be unused on functions with no
            # parameters). To simplify that handling without having to pass the
            # number of parameters from the function definition to the function
            # block, which is cumbersome:
            # - start the generator index at 1
            # - have the parameters use index-1
            # - have the local variables use index
            generator.current_register = 1

        elif (node.data == "compound_statement"):
            generator.symbol_table.push_scope()

        #
        # in order visit
        #

        if ((len(node.children) == 1) and (node.data not in ["init_declarator", "init_declarator_list"])):
            # XXX This catch all should go at some point?
            # XXX This is cumbersome because sometimes we want to always receive
            #     a list to unify handling, on the other hand creating a one 
            #     element list here would do nesting, but it's also convenient
            # XXX Ideally we should handle all nodes individually without catch-all
            gen_node = generate_ir(generator, node.children[0])
                
        else:
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
                gen_node = generate_ir(generator, node.children[1])

            elif (node.data == "cast_expression"):
                # cast_expression: ... |  "(" type_name ")" cast_expression
                gen_node = [
                    generate_ir(generator, node.children[1]),
                    generate_ir(generator, node.children[3])
                ]    

            elif (node.data == "expression_statement"):
                # expression_statement:  expression? ";"
                if (len(node.children) > 1):
                    gen_node = generate_ir(generator, node.children[0])

            elif ((node.data == "jump_statement") and (node.children[0].value == "return")):
                # jump_statement: ... | "return" expression? ";" | ...
                if (len(node.children) > 2):
                    gen_node = generate_ir(generator, node.children[1])

            elif (node.data == "primary_expression"):
                # primary_expression: ... | "(" expression ")" | ..
                # The other primary expression rules go through the single-child 
                # path
                assert (len(node.children) == 3)
                gen_node = generate_ir(generator, node.children[1])

            elif (node.data == "init_declarator_list"):
                # init_declarator_list:  init_declarator
                # |  init_declarator_list "," init_declarator 
                # Skip the commas, pass the rest
                
                if (len(node.children) == 1):
                    gen_node = [generate_ir(generator, node.children[0])]
                else:
                    gen_node = generate_ir(generator, node.children[0])
                    gen_node.append(generate_ir(generator, node.children[2]))
                    

            elif (node.data == "init_declarator"):
                # declarator contains one identifier and one or none initializers
                # init_declarator:  declarator
                # |  declarator "=" initializer
                identifier = get_tree_tokens(node.children[0])[0]
                initializer = None
                if (len(node.children) > 1):
                    initializer = generate_ir(generator, node.children[2])
                gen_node = [identifier, initializer]

            elif (node.data == "declaration"):
                # declaration contains one type and one or more identifiers and or
                # initializerss
                # declaration:  declaration_specifiers init_declarator_list? ";"

                # XXX Right now all variables are allocated on the stack, globals
                #     not supported
                assert (len(generator.symbol_table) > 1), "Global variables not supported yet!"
                
                # The declarator list may contain initializer so it needs
                # generating
                if (len(node.children) > 1):
                    gen_node = generate_ir(generator, node.children[1])
                    
                # Register the variable and create an IR node to hold the
                # initializer, if any
                irs = []
                externs = set()
                decl_type = get_tree_tokens(node.children[0])[0]
                for identifier, initializer in gen_node:
                    variable = Struct(
                        type="variable", 
                        name=identifier, 
                        value_type=decl_type,
                        # Value_reg and value_ref will be assigned on usage
                    )
                    generator.symbol_table[identifier] = variable
                    if (initializer is not None):
                        # XXX This should come from gen_node instead of having
                        #     to recreate it here?
                        a = Struct(type="identifier", value=identifier)
                        b = initializer

                        # XXX This is a copy-paste of the operation, refactor

                        a_ref, a_reg, a_type = get_ref_reg_and_type(a, irs, externs)
                        b_reg, b_type = get_reg_and_type(b, irs, externs)

                        res_type = a_type

                        # Convert the input type to the result type
                        if (b_type != res_type):
                            b_reg = generate_call_ir(generator, irs, externs, 
                                get_fn_name("cnv", res_type, b_type), res_type, [b_type, b_reg])

                        a_llvm_type = get_llvm_type(a_type)
                        
                        # Perform the store
                        # "store i32 %0, i32* %3, align 4"
                        irs.append("store %s %%%d, %s* %%%d, align 4" % (a_llvm_type, b_reg, a_llvm_type, a_ref))    
                        
                gen_node = Struct(type="ir", value_type="void", value_reg=None, value=irs, externs=externs)

            elif (node.data == "parameter_list"):
                # parameter_list:  parameter_declaration 
                #   |  parameter_list "," parameter_declaration
                # Remove extra nesting so the parameter list location is
                # deterministic at parameter collection time in the function
                # wrap-up
                gen_node = [generate_ir(generator, node.children[0])]
                if (len(node.children) > 1):
                    gen_node.append(generate_ir(generator, node.children[2]))

            elif (node.data == "block_item_list"):
                # block_item_list:  block_item
                # |  block_item_list block_item
                gen_node = generate_ir(generator, node.children[0])
                if (len(node.children) > 1):
                    # Merge blocks into a single ir node
                    prev_node = gen_node
                    gen_node = generate_ir(generator, node.children[1])
                    gen_node.value = prev_node.value + gen_node.value
                    gen_node.externs.update(prev_node.externs)

            else:
                gen_node = []
                for child in node.children:
                    gen_node.append(generate_ir(generator, child))

        #
        # After children visit actions
        #
        
        # trap any expression that hasn't been resolved to a single node
        if ((node.data == "type_name") or (node.data == "declaration_specifiers")):
            # XXX Right now assume there's only one type and it's one of the
            #     basic types, once complex types are supported, the types will
            #     have to be put in the symbol table and parsed properly
            res_type = get_tree_tokens(node)
            res_type = string.join(res_type, " ")
            gen_node = res_type
                
        elif (node.data == "cast_expression"):
            # Always make sure expressions leave as IR nodes. 
            # This takes care of not pushing constants and identifiers up
            # XXX This could be done earlier in primary_expression
            # XXX This may make harder detecting constants upstream?
            
            # Convert to type if different, otherwise pass through making sure
            # an IR node is created if necessary
            if (isinstance(gen_node, list)):
                # There's a cast operator, deal with it
                res_type = gen_node[0]
                gen_node = gen_node[1]
            else:
                res_type = None
            
            irs = []
            externs = set()
            a_reg, a_type = get_reg_and_type(gen_node, irs, externs)
            
            if ((res_type is not None) and (res_type != a_type)):
                res_reg = generate_call_ir(generator, irs, externs,
                    get_fn_name("cnv", res_type, a_type), res_type, [a_type, a_reg])
            else:
                res_reg = a_reg
                res_type = a_type

            gen_node = Struct(type="ir", value_type=res_type, value_reg=res_reg, value=irs, externs=externs)

        elif (node.data.endswith("_expression") and isinstance(gen_node, list) and (len(gen_node) == 3)):
            
            a, op_sign, b = gen_node

            if (op_sign in ass_ops):
                if (len(op_sign) > 1):
                    # assing + operation, generate "a += b" as "a = a + b"
                    b = generate_binop_ir(generator, a, b, op_sign[:-1])
                    
                gen_node = generate_assign_ir(generator, a, b)
                
            else:

                gen_node = generate_binop_ir(generator, a, b, op_sign)

        elif (node.data == "parameter_declaration"):
            # ['float', 'a']
            parameter_type, parameter_name = gen_node
            parameter = Struct(
                type="variable", 
                name=parameter_name.value, 
                value_type=parameter_type, 
                # LLVM needs a gap of 1 between parameters and local vars
                value_reg=generator.current_register - 1
            )
            
            generator.symbol_table.set_overflow_item(parameter_name.value, parameter)
            generator.current_register += 1

            gen_node = parameter
            
        elif (node.data == "compound_statement"):
            generator.symbol_table.pop_scope()
            
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

        elif (node.data == "function_definition"):
            fn = generator.symbol_table[function_name]
            fn.parameters = []
            
            # Collect parameters, note they could be empty or just one
            parameters = []
            parameter_nodes = gen_node[1][2]
            if (parameter_nodes != ")"):
                if (not isinstance(parameter_nodes, list)):
                    parameter_nodes = [parameter_nodes]
                for parameter_node in parameter_nodes:
                    # [{value_reg: 0, value... variable}, ',', {value_reg: 1, value... variable}]
                    if (parameter_node != ","):
                        parameters.append(parameter_node)
                        
            fn.parameters = parameters
            if (gen_node[2] == "}"):
                # Empty function
                fn.ir = Struct(type="ir", value_type="void", value_reg=None, value=[], externs=set())
            else:
                fn.ir = gen_node[2]

            # If the return type is different from the block type, convert
            if (fn.value_type != fn.ir.value_type):
                a_type = fn.ir.value_type
                a_reg = fn.ir.value_reg
                res_type = fn.value_type
                irs = fn.ir.value
                externs = fn.ir.externs
                res_reg = generate_call_ir(generator, irs, externs,
                    get_fn_name("cnv", res_type, a_type), res_type, [a_type, a_reg])
        
                fn.ir = Struct(type="ir", value_type=res_type, value_reg=res_reg, value=irs, externs=externs)
                
            # Now really return the value
            # ret float %11, !dbg !29
            # ret void, !dbg !33
            irs = fn.ir.value
            externs = fn.ir.externs
            res_reg = fn.ir.value_reg
            res_type = fn.ir.value_type
            if (fn.value_type == "void"):
                irs.append("ret void")

            else:
                # Note this can be a straight constant, so always create a new
                # ir node and get the reg and type
                irs = []
                externs = set()
                res_reg, res_type = get_reg_and_type(fn.ir, irs, externs)
                irs.append("ret %s %%%d" % (get_llvm_type(res_type), res_reg))
                
            fn.ir = Struct(type="ir", value_type=res_type, value_reg=res_reg, value=irs, externs=externs)

            # Right now we generate IR with unique register indices but not
            # guaranteed to be monotonically increasing wrt where they appear in
            # the sequence of instructions. The indices are allocated at
            # expression parsing time, which is not the same as instruction
            # execution order (eg in the presence of parenthesis,etc). LLVM will
            # error out if it finds non-monotonically increasing register
            # indices. Reindex the registers so they are monotonically
            # increasing wrt instruction execution order

            # XXX This should be fixed by using llvmlite's IrBuilder instead 
            #     of strings?
            
            # Build the reindexing table, initialize with the parameters and the
            # empty gap
            index_to_index = { i : i for i in xrange(len(fn.parameters)+1) }
            for m in re.finditer(r"%(\d+)", string.join(fn.ir.value, "")):
                reg_index = int(m.group(1))
                if (reg_index not in index_to_index):
                    index_to_index[reg_index] = len(index_to_index)

            # Perform the replacement
            fn.ir.value = [
                re.sub("%(\d+)", lambda m: "%%%d" % index_to_index[int(m.group(1))], value)
                for value in fn.ir.value
            ]
                
            gen_node = generator.symbol_table[function_name]
        
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
        pmb.loop_vectorize = loop_vectorize
        pmb.slp_vectorize = slp_vectorize
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
    #     LALR(1) and switch to LALR parser. Optionallly once epycc can
    #     bootstrap itself and write the parser code code in C (the AST
    #     traversal can remain in Python).
    grammar_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 
        "grammars", "c99_phrase_structure_grammar.lark")
    parser = lark.Lark.open(grammar_filepath, keep_all_tokens="True", 
        lexer="standard")
    tree = parser.parse(source)

    if (debug):
        print tree.pretty()

    # XXX Do a proper symbol table with multi-level lookup (each level is a different
    #     scope)
    generator = Struct(symbol_table = SymbolTable(), current_register = 0)
    irs = generate_ir(generator, tree)

    epycc_dirpath = os.path.dirname(os.path.abspath(__file__))
    generated_c_filepath = os.path.join(epycc_dirpath, "generated", "irs.c")
    generated_ir_filepath = os.path.join(epycc_dirpath, "generated", "irs.ir")

    # Regenerate if they don't exist or same or older than this python file
    if (not os.path.exists(generated_ir_filepath) or
        (os.path.getmtime(generated_ir_filepath) <= os.path.getmtime(__file__))):
        precompile_c_snippets(generated_c_filepath, generated_ir_filepath)
    
    all_externs = load_functions_ir(generated_ir_filepath)
    
    llvm_ir = []
    function_signatures = []
    function_externs = set()
    assert len(generator.symbol_table) == 1, "Symbol table is not at global scope!!!"
    for sym in generator.symbol_table.values():
        if (sym.type == "function"):
            
            # Collect externs
            function_externs.update(sym.ir.externs)

            # Dump this function's IR
            #   define dso_local zeroext i16  @add__int__int__short(i32, i16 zeroext) {
            # LLVM type extension goes first in function return value, last on function parameters
            llvm_ir.append("define dso_local %s %s @%s(%s) {" % (
                get_llvm_type_ext(sym.value_type),
                get_llvm_type(sym.value_type),
                sym.name,
                string.join([
                    
                    ("%s %s" % (get_llvm_type(parameter.value_type), get_llvm_type_ext(parameter.value_type)))
                    for parameter in sym.parameters], ",")
            ))

            for l in sym.ir.value:
                llvm_ir.append("  " + l)
            llvm_ir.append("}")
            llvm_ir.append("")
            llvm_ir.append("")
            
            function_signature = Struct(
                name=sym.name, 
                ctypes = [get_ctype(sym.value_type)] + 
                    [get_ctype(parameter.value_type) for parameter in sym.parameters]
            )

            function_signatures.append(function_signature)

    for function_extern in function_externs:
        # Dump the extern functions needed by this function
        extern = all_externs[function_extern]
        llvm_ir.append(extern[0])
        for l in extern[1:-1]:
            llvm_ir.append("  " + l)
        llvm_ir.append(extern[-1])
        llvm_ir.append("")

    llvm_ir = string.join(llvm_ir, "\n")

    return llvm_ir, function_signatures


def epycc_compile(source, debug = False):
    # XXX This does reinitialization when called multiple times and causes 
    #     warnings like 
    #       :for the -x86-asm-syntax option: may only occur zero or one times!
    #     Do proper tear down or return some kind of singleton

    llvm_ir, function_signatures = epycc_generate(source, debug)
    lib = llvm_compile(llvm_ir, function_signatures)

    return lib



if (__name__ == "__main__"):
    source = open("samples/current.c").read()

    lib = epycc_compile(source, True)
    print lib.ir
    print lib.ir_optimized
    #print lib.asm
    #print lib.asm_optimized
    print lib.f2pow2(2)

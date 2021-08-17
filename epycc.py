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


def unpack_ops(ops):
    return [ (op.split(":")[0], op.split(":")[1]) for op in ops]

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

def get_binop_fn_name(*args):
    """
    Return the given binop sign and argumetns function name
    """
    return get_fn_name( *([binop_sign_to_name[args[0]]] + list(args[1:])) )

def get_llvm_type(t):
    """
    Return the llvm type corresponding to a C type
    """
    # XXX Use some of the existing type list or snippets to build this?
    # XXX Calling this on every IR generation is error prone, should we 
    #     store this type in the symbol and have get_llvm_result_type, etc?
    #     Or just keep the llvm type around?
    c_to_irtypes = {
        "double" : "double",
        "float" : "float",
        "long long" :"i64",
        "signed long long" : "i64",
        "unsigned long long" : "i64", 
        "long": "i32",
        "signed long" : "i32",
        "unsigned long" : "i32",
        "int" : "i32",
        "signed int": "i32",
        "unsigned int" : "i32",
        "short" : "signext i16",
        "signed short" : "signext i16",
        "unsigned short" : "zeroext i16",
        "char" : "signext i8",
        "signed char" : "signext i8",
        "unsigned char" : "zeroext i8",
    }
    # Make sure we are covering all qualified types
    assert all((qualif_types.index(c_type) >= 0) for c_type in c_to_irtypes)
    assert all((qualif_type in c_to_irtypes) for qualif_type in qualif_types)
    return c_to_irtypes[t]

def get_ctype(t):
    """
    Return the ctype corresponding to a C type
    """
    c_to_ctypes = {
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
    }
    # Make sure we are covering all qualified types
    assert all((qualif_types.index(c_type) >= 0) for c_type in c_to_ctypes)
    assert all((qualif_type in c_to_ctypes) for qualif_type in qualif_types)
    return c_to_ctypes[t]    


binops = unpack_ops([
    "+:add", "-:sub", "*:mul", "/:div", "%:mod",
    "<<:lshift", ">>:rshift",
    "<:lt", "<=:lte", ">:gt", ">=:gte","==:eq", "!=:neq",
    "&:bitand", "|:bitor", "^:bitxor",
    "&&:and", "||:or",
])

# XXX Missing post-pre incr
prepostops = [ "++", "--" ]

unops = unpack_ops(["+:add", "-:sub", "~:bitnot", "!:not"])

types = ["char", "short", "int", "long", "long long", "float", "double"]

integer_types = set(["char", "short", "int", "long", "long long"])
float_types = set(["float", "double"])
# XXX Remove signed versions which map to plain anyway, and map to the non
#     qualified type and standardize on unsigned and plain at symbol table
#     creation time?
integer_qualifs = ["unsigned", "signed"]

qualif_types = types + [integer_qualif + " " + integer_type for integer_qualif in integer_qualifs for integer_type in integer_types]

int_ops = set(["|", "&", "^", "%", "<<", ">>", "!", "~"])
rel_ops = set(["<", "<=", ">", ">=","==", "!="])
logic_ops = set(["&&", "||"])

binop_sign_to_name = { binop_sign : binop_name for binop_sign, binop_name in binops }

# XXX Missing memory operators * . -> &
# XXX Missing cast operator? ()

def precompile_c_snippets():
    """
    Generate a file containing one C function implementing every C operation and
    type.

    The generated file can then be fed to a local clang install via

        %CLANG% -mllvm -S -std=c99 --x86-asm-syntax=intel -emit-llvm -o- generated/irs.c

    and generate LLVM IR that can be read from epycc to do the runtime codegen (the
    asm-syntax option is needed so clang doesn't error trying to generate object
    code)

    """
    l = []

    # Operations are done in the same type and then the result converted 
    # using a conversion function

    # XXX Should the forced cast exist? The highest ranked type is used for
    #     operations anyway at codegen time?
            
    for unop_sign, unop_name in unops:
        for c_type in qualif_types:
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
        for c_type in qualif_types:
            # Don't do integer-only operations (bitwise, mod) on non-integer
            # operands
            if ((binop_sign in int_ops) and (c_type not in integer_types)):
                continue

            # char add__char__char__char(char a, char b) { return (char) (a + b); }
            fn = "%s %s(%s a, %s b) { return (%s) (a %s b); }" % (
                c_type, 
                get_binop_fn_name(binop_sign, c_type, c_type, c_type),
                c_type, c_type,
                c_type,
                binop_sign,
            )

            l.append(fn + "\n")

            # Assignment operators will be done as a = a + b
                
    # Build the type conversion functions
    for res_type in qualif_types:
        for a_type in qualif_types:
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
    with open("generated/irs.c", "w") as f:
        f.writelines(l)

    # Generate the precompiled IR in irs.ir
    clang_filepath = R"C:\android-ndk-r15c\toolchains\llvm\prebuilt\windows-x86_64\bin\clang.exe"
    cmd = R"%s -mllvm --x86-asm-syntax=intel -S -std=c99 -emit-llvm -o generated/irs.ir generated/irs.c" % (
        clang_filepath
    )
    os.system(cmd)


def generate_ir(generator, node):
    def get_grandson(node, parent_indices):
        if (len(parent_indices) > 0):
            node = get_grandson(node.children[parent_indices[0]], parent_indices[1:])
        return node

    def get_result_type(type_a, type_b):
        # Types sorted by highest rank first
        # XXX Review this really matches the c99 rank or find a way of 
        #     extracting the result type of the C snippets or from some table
        #     we build out of invoking clang with all the different combinations
        types_highest_rank_first = [ 
            "double",
            "float",
            "long long",
            "signed long long",
            "unsigned long long",
            "long",
            "signed long",
            "unsigned long",
            "int",
            "signed int",
            "unsigned int",
            "short",
            "signed short",
            "unsigned short",
            "char",
            "signed char",
            "unsigned char",
        ]
        # XXX This could use a dict for faster lookups
        result_type_index = min(
            types_highest_rank_first.index(type_a), 
            types_highest_rank_first.index(type_b)
        )

        return types_highest_rank_first[result_type_index]

    def get_reg_and_type(a, irs, externs):
        """
        Get reg and type for a given source or destination operand

        Generate the necessary instructions and extern references in case a is
        not in a register yet (identifier or constants)
        """

        if (a.type == "identifier"):
            a = generator.symbol_table[-2][a.value]
            # Allocate a register and store if this symbol doesn't have one
            # yet
            if (not hasattr(a, "value_reg")):
                a.value_reg = generator.current_register
                generator.current_register += 1

                irs.append("%%%d = alloca %s, align 4" % (a.value_reg, get_llvm_type(a.value_type)))

            a_reg = a.value_reg
            a_type = a.value_type

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

    def generate_ir_call(generator, irs, externs, fn_name, res_type, type_reg_list):
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
                get_grandson(node, (0, 0, 0)).value, 
                get_grandson(node, (1, 0, 0, 0, 0)).value
            )
            generator.symbol_table[-2][function_name] = Struct(type = "function", name=function_name, value_type=function_type)
            
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
            generator.symbol_table.append({})

        #
        # in order visit
        #

        if (len(node.children) == 1):
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

            elif (node.data == "parameter_list"):
                # parameter_list:  parameter_declaration 
                #   |  parameter_list "," parameter_declaration
                # Remove extra nesting so the parameter list location is
                # deterministic at parameter collection time in the function
                # wrap-up
                gen_node = [generate_ir(generator, node.children[0])]
                if (len(node.children) > 1):
                    gen_node.append(generate_ir(generator, node.children[2]))
                
            else:
                gen_node = []
                for child in node.children:
                    gen_node.append(generate_ir(generator, child))

        #
        # After children visit actions
        #
        
        # trap any expression that hasn't been resolved to a single node
        if ((node.data == "jump_statement") and (node.children[0].value == "return")):
            # Generate the return code
            # ret float %11, !dbg !29
            # ret void, !dbg !33
            externs = set()
            if (gen_node is None):
                irs = ["ret void"]

            else:
                # Note this can be a straight constant, so always create a new
                # ir node and get the reg and type
                irs = []
                
                ret_reg, ret_type = get_reg_and_type(gen_node, irs, externs)
                # XXX Needs to truncate to function's return value type but
                #     it's not available here?
                #   %8 = fptrunc double %7 to float, !dbg !9670
                # XXX Do this with snippets
                irs.append("ret %s %%%d" % (get_llvm_type(ret_type), ret_reg))
                
            gen_node = Struct(type="ir", value_type=None, value_reg=None, value=irs, externs=externs)
                
        elif (node.data.endswith("_expression") and ((type(gen_node) is list) and len(gen_node) == 3)):
            
            a, op_sign, b = gen_node

            irs = []
            externs = set()

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
            res_type = get_result_type(a_type, b_type)

            # Convert the input types to the result type
            if (a_type != res_type):
                a_reg = generate_ir_call(generator, irs, externs,
                    get_fn_name("cnv", res_type, a_type), res_type, [a_type, a_reg])
                
            if (b_type != res_type):
                b_reg = generate_ir_call(generator, irs, externs, 
                    get_fn_name("cnv", res_type, b_type), res_type, [b_type, b_reg])

            # Perform the operation in res_type
            fn_name = get_binop_fn_name(op_sign, res_type, res_type, res_type)
            res_reg = generate_ir_call(generator, irs, externs, fn_name, 
                res_type, [res_type, a_reg, res_type, b_reg])

            gen_node = Struct(type="ir", value_type=res_type, value_reg = res_reg, value=irs, externs=externs)

        elif (node.data == "parameter_declaration"):
            # ['float', 'a']
            parameter_type, parameter_name = gen_node
            # Parameters go into the overflow table
            parameter = Struct(
                type="variable", 
                name=parameter_name.value, 
                value_type=parameter_type, 
                # LLVM needs a gap of 1 between parameters and local vars
                value_reg=generator.current_register - 1
            )
            generator.symbol_table[-1][parameter_name.value] = parameter
            generator.current_register += 1

            gen_node = parameter
            
        elif (node.data == "compound_statement"):
            generator.symbol_table.pop()
            # Clear the overflow table
            generator.symbol_table[-1] = {}

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
            fn = generator.symbol_table[-2][function_name]
            fn.parameters = []
            
            # Collect parameters, note they could be empty or just one
            parameters = []
            parameter_nodes = gen_node[1][2]
            if (parameter_nodes != ")"):
                if (type(parameter_nodes) is not list):
                    parameter_nodes = [parameter_nodes]
                for parameter_node in parameter_nodes:
                    # [{value_reg: 0, value... variable}, ',', {value_reg: 1, value... variable}]
                    if (parameter_node != ","):
                        parameters.append(parameter_node)
                        
            fn.parameters = parameters
            fn.ir = gen_node[2]


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
                
            gen_node = generator.symbol_table[-2][function_name]
        
    elif (type(node) is lark.Token):
        gen_node = node.value

    else:
        assert False, "Unexpected node type %s" % node
    
    return gen_node



def llvm_compile(llvm_ir, function_signatures):
    # This switches the assembler emit from at&t to intel, needs to be done
    # before initializing llvmlite, otherwise it's ignored
    # XXX This probably doesn't affect the input assembler, only the output, which
    #     has to be done in AT&T eg
    #       call void asm sideeffect "movl %eax, %eax", "~{dirflag},~{fpsr},~{flags}"() #2
    llvm.set_option('', '--x86-asm-syntax=intel')

    # All these initializations are required for code generation!
    llvm.initialize()
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()  # yes, even this one

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


def epycc_compile(source):
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
    parser = lark.Lark.open('grammars/c99_phrase_structure_grammar.lark', 
      keep_all_tokens="True", lexer="standard", debug=True)
    tree = parser.parse(source)

    ## print tree.pretty()

    # XXX Do a proper symbol table with multi-level lookup (each level is a different
    #     scope)
    generator = Struct(symbol_table=[{}, {}], current_register = 0)
    irs = generate_ir(generator, tree)

    ## print irs
    ## print generator.symbol_table

    all_externs = {}
    with open("generated/irs.ir", "r") as f:
        extern = None
        for l in f:
            # define signext i8 @add__char__char__char(i8 signext, i8 signext) #0 {
            l = l.strip()
            if (l.endswith("{")):
                extern = []
                m = re.search("@([^(]+)", l)
                extern_name = m.group(1)
        
            if (extern is not None):
                extern.append(l)
        
            if (l.endswith("}")):
                all_externs[extern_name] = extern
                extern = None

    llvm_ir = []
    function_signatures = []
    function_externs = set()
    for sym in generator.symbol_table[0].values():
        if (sym.type == "function"):
            
            # Collect externs
            function_externs.update(sym.ir.externs)

            # Dump this function's IR
            # define dso_local float @fadd(float, float) #0 {
            llvm_ir.append("define dso_local %s @%s(%s) {" % (
                get_llvm_type(sym.value_type),
                sym.name,
                string.join([get_llvm_type(parameter.value_type) for parameter in sym.parameters], ",")
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

    lib = llvm_compile(llvm_ir, function_signatures)

    return lib

if (__name__ == "__main__"):
    
    if (not os.path.exists("generated/irs.ir")):
        print "Precompiling C snippets"
        precompile_c_snippets()

    source = open("tests/ops.c").read()

    lib = epycc_compile(source)
    print lib.ir
    print lib.ir_optimized
    #print lib.asm
    #print lib.asm_optimized
    print lib.f2pow2(2)

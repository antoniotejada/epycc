# Epycc: Embedded Python C Compiler

Embedded Python C Compiler is a Python 2.7 module to JIT compile and optimize C code to be used seamlessly from Python code as if it had been implemented in Python, but with the speed of optimized C.

```python
c_code = """
float f2pow2(int a) {
    return 2.0f * (a * a);
}
"""
jit_lib = epycc_compile(c_code)
print jit_lib.f2pow2(2)
```
```python
8.0
```

Internally it generates LLVM IR and uses [llvmmlite](https://github.com/numba/llvmlite/) to JIT compile it into executable machine code.

The generated LLVM IR code calls into LLVM IR snippets pregenerated from C code. This is in order to accelerate epycc development and perform some tiring tasks like C99-compliant type conversion, etc:

```python
print jit_lib.f2pow2.ir
```
```LLVM
define float @mul__float__float__int(float, i32) #0 {
   %3 = alloca float, align 4
   %4 = alloca i32, align 4
   store float %0, float* %3, align 4
   store i32 %1, i32* %4, align 4
   %5 = load float, float* %3, align 4
   %6 = load i32, i32* %4, align 4
   %7 = sitofp i32 %6 to float
   %8 = fmul float %5, %7
   ret float %8
}

define i32 @mul__int__int__int(i32, i32) #0 {
   %3 = alloca i32, align 4
   %4 = alloca i32, align 4
   store i32 %0, i32* %3, align 4
   store i32 %1, i32* %4, align 4
   %5 = load i32, i32* %3, align 4
   %6 = load i32, i32* %4, align 4
   %7 = mul nsw i32 %5, %6
   ret i32 %7
}

define dso_local float @f2pow2(i32) {
   %2 = alloca float, align 4
   store float 2.0, float* %2, align 4
   %3 = load float, float* %2, align 4
   %4 = call i32 @mul__int__int__int(i32 %0, i32 %0)
   %5 = call float @mul__float__float__int(float %3, i32 %4)
   ret float %5
}
```

Note using those snippets doesn't suppose any performance issues because LLVM optimizes them away inlining the calls and removing any unnecessary load/stores:

```python
 print jit_lib.f2pow2.optimized_ir
```
```LLVM
define dso_local float @f2pow2(i32) local_unnamed_addr #0 {
  %2 = mul nsw i32 %0, %0
  %3 = sitofp i32 %2 to float
  %4 = fmul float %3, 2.000000e+00
  ret float %4
}
```

```python
 print jit_lib.f2pow2.asm
```
```assembly
        .text
        .intel_syntax noprefix
        .file   "<string>"
        .globl  f2pow2
        .p2align        4, 0x90
        .type   f2pow2,@function
f2pow2:
        imul    ecx, ecx
        cvtsi2ss        xmm0, ecx
        addss   xmm0, xmm0
        ret
```

Since the module includes a full featured C parser, it can also be used to parse and inspect C code.

## Current functionality
- [x] Parse most of C99 code
- [x] Generate IR for simple expressions
- [x] Execute generated IR seamlessly like a Python function


## Future functionality
- [ ] Full C99 language support
- [ ] Assembler support
- [ ] "ctypable" transparent Python parameter passing support
- [ ] Packaging
- [ ] Publishing to Pypi
- [ ] External function calling from inside C
- [ ] Python function calling from inside C
- [ ] Spilling generated IR or executable to disk for distribution
- [ ] C runtime
- [ ] C preprocessor, include file support
- [ ] Compile arbitrary C sources (and call external DLL/so functions)
- [ ] Python 3.x compatible


# Implementation details
- C99 grammar straight and unmodified from the 9899:1999 spec
- Clang for precompiling C code into IR snippets that get called internally.
- [Lark](https://github.com/lark-parser/lark) for parsing
- [llvmmlite](https://github.com/numba/llvmlite/) for JIT compiling LLVM IR into executable code.
# Epycc: Embedded Python C Compiler

Embedded Python C Compiler is a Python 2.7 module that allows JIT compiling and invoking C code seamlessly from Python, as if it was a Python function, but at native non-interpreted execution speeds.

```python
c_code = """
float f2pow2(int a) {
    return 2.0f * (a * a);
}
"""
lib = epycc_compile(c_code)
print lib.f2pow2(2)
```
```python
8.0
```

Internally it generates LLVM IR and uses [llvmlite](https://github.com/numba/llvmlite/) to JIT compile it into executable machine code in memory.

The generated LLVM IR code calls into LLVM IR snippets pregenerated from C code. This is in order to accelerate epycc development and perform some brittle tasks like C99-compliant type conversion, etc:

```python
print lib.ir
```
```LLVM
; ModuleID = '<string>'
source_filename = "<string>"
target datalayout = "e-m:e-i64:64-f80:128-n8:16:32:64-S128"

define float @f2pow2(i32 %.1) {
entry:
  %.3 = alloca i32
  store i32 %.1, i32* %.3
  %.5 = load i32, i32* %.3
  %.6 = load i32, i32* %.3
  %.7 = call i32 @mul__int__int__int(i32 %.5, i32 %.6)
  %.8 = call float @cnv__float__int(i32 %.7)
  %.9 = call float @mul__float__float__float(float 2.000000e+00, float %.8)
  ret float %.9
}

define dso_local float @cnv__float__int(i32) {
  %2 = alloca i32, align 4
  store i32 %0, i32* %2, align 4
  %3 = load i32, i32* %2, align 4
  %4 = sitofp i32 %3 to float
  ret float %4
}

define dso_local float @mul__float__float__float(float, float) {
  %3 = alloca float, align 4
  %4 = alloca float, align 4
  store float %1, float* %3, align 4
  store float %0, float* %4, align 4
  %5 = load float, float* %4, align 4
  %6 = load float, float* %3, align 4
  %7 = fmul float %5, %6
  ret float %7
}

define dso_local i32 @mul__int__int__int(i32, i32) {
  %3 = alloca i32, align 4
  %4 = alloca i32, align 4
  store i32 %1, i32* %3, align 4
  store i32 %0, i32* %4, align 4
  %5 = load i32, i32* %4, align 4
  %6 = load i32, i32* %3, align 4
  %7 = mul nsw i32 %5, %6
  ret i32 %7
}

; Function Attrs: nounwind
declare void @llvm.stackprotector(i8*, i8**) #0

attributes #0 = { nounwind }
```

Note using those snippets doesn't suppose any performance issues because LLVM optimizes them away inlining the calls and removing any unnecessary load/stores:

```python
print lib.ir_optimized
```
```LLVM
; ModuleID = '<string>'
source_filename = "<string>"
target datalayout = "e-m:e-i64:64-f80:128-n8:16:32:64-S128"

; Function Attrs: norecurse nounwind readnone
define float @f2pow2(i32 %.1) local_unnamed_addr #0 {
entry:
  %0 = mul nsw i32 %.1, %.1
  %1 = sitofp i32 %0 to float
  %2 = fmul float %1, 2.000000e+00
  ret float %2
}

; Function Attrs: norecurse nounwind readnone
define dso_local float @cnv__float__int(i32) local_unnamed_addr #0 {
  %2 = sitofp i32 %0 to float
  ret float %2
}

; Function Attrs: norecurse nounwind readnone
define dso_local float @mul__float__float__float(float, float) local_unnamed_addr #0 {
  %3 = fmul float %0, %1
  ret float %3
}

; Function Attrs: norecurse nounwind readnone
define dso_local i32 @mul__int__int__int(i32, i32) local_unnamed_addr #0 {
  %3 = mul nsw i32 %1, %0
  ret i32 %3
}

attributes #0 = { norecurse nounwind readnone }
```

```python
print lib.asm_optimized
```
```assembly
	.text
	.intel_syntax noprefix
	.file	"<string>"
	.globl	f2pow2
	.p2align	4, 0x90
	.type	f2pow2,@function
f2pow2:
	imul	ecx, ecx
	cvtsi2ss	xmm0, ecx
	addss	xmm0, xmm0
	ret
.Lfunc_end0:
	.size	f2pow2, .Lfunc_end0-f2pow2

	.globl	cnv__float__int
	.p2align	4, 0x90
	.type	cnv__float__int,@function
cnv__float__int:
	cvtsi2ss	xmm0, ecx
	ret
.Lfunc_end1:
	.size	cnv__float__int, .Lfunc_end1-cnv__float__int

	.globl	mul__float__float__float
	.p2align	4, 0x90
	.type	mul__float__float__float,@function
mul__float__float__float:
	mulss	xmm0, xmm1
	ret
.Lfunc_end2:
	.size	mul__float__float__float, .Lfunc_end2-mul__float__float__float

	.globl	mul__int__int__int
	.p2align	4, 0x90
	.type	mul__int__int__int,@function
mul__int__int__int:
	mov	eax, ecx
	imul	eax, edx
	ret
.Lfunc_end3:
	.size	mul__int__int__int, .Lfunc_end3-mul__int__int__int


	.section	".note.GNU-stack","",@progbits
```

Since the module includes a full featured C parser, it can also be used to parse and inspect C code.

## Current functionality
- [x] Parse most of C99 code
- [x] Generate IR for floating point and integer expressions
- [x] Generate IR for functions
- [x] Generate IR for assigning and reading from function parameters and local scalar variables
- [x] Generate IR for if then / else statements
- [x] Execute generated IR seamlessly like a Python function
- [x] "ctypable" transparent Python parameter passing support

## Future functionality
- [ ] Generate IR for function calls
- [ ] Generate IR for for/while statements
- [ ] Generate IR for switch statements
- [ ] Generate IR for arrays, pointers
- [ ] Generate IR for structs, user defined types, bitfields
- [ ] Parse all C99 code
- [ ] Assembler support
- [ ] Packaging into a proper Python package
- [ ] Publishing to Pypi
- [ ] External native function calling from inside C
- [ ] Python function calling from inside C
- [ ] Spilling generated IR or executable to disk for distribution
- [ ] C runtime, invoking Python's in-process loaded runtime via external native function calls
- [ ] C preprocessor, include file support
- [ ] Compile arbitrary C sources (and call external DLL/so functions)
- [ ] Python 3.x compatible


# Implementation details
- C99 grammar straight and unmodified from the 9899:1999 spec
- Clang for precompiling C code into IR snippets that get called internally.
- Generated code validation via comparison vs. clang-generated code
- [Lark](https://github.com/lark-parser/lark) for parsing
- [llvmlite](https://github.com/numba/llvmlite/) for JIT compiling LLVM IR into executable code.

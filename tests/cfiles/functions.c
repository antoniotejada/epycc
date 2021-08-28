// No arguments forward declaration
int fnoargs();

// No arguments
int fnoargs() {
    return 5;
}

int fnoargs_caller() {
    return fnoargs();
}

// Single argument forward declaration
int fonearg(int a);

// Single argument

int fonearg(int a) {
    return a;
}

int fonearg_caller(int a) {
    return fonearg(a);
}

// Multiple arguments forward declaration
int fthreeargs(int a, int b, int c);

// Multiple arguments

int fthreeargs(int a, int b, int c) {
    return a + b + c;
}

int fthreeargs_caller(int a, int b, int c) {
    return fthreeargs(a, b, c);
}

// Different parameter types

float ffloat(float a) {
    return fonearg_caller(a);
}

// Recursive functions
int ffib(int a) {
    if (a == 0) {
        return 0;
    } else if (a == 1) {
        return 1;
    } else {
        return ffib(a-1) + ffib(a-2);
    }
}

int ffact(int a) {
    if (a == 0) {
        return 1;
    }
    return a * ffact(a-1);
}

int fsum(int a) {
    if (a == 0) {
        return 0;
    }
    return a + fsum(a-1);
}


// Indirect recursive functions
// XXX These have 5 mismatches, looks like epycc is doing last call optimization
//     while clang isn't? Should look further into it, clang seems to have the
//     inline threshold around 25 for -O2 (with "-mllvm --inline-threshold=21"
//     produces the same code as without, and different code than with 20)
// XXX In addition, clang inserts a phi node to unify return paths while epycc
//     doesn't (and is one jump shorter because of that)
int fsum_indirect2__mm5(int a);
int fsum_indirect1__mm5(int a) {
    if (a == 0) {
        return 0;
    }
    return (a * 2) + fsum_indirect2__mm5(a - 1);
}

int fsum_indirect2__mm5(int a) {
    if (a == 0) {
        return 0;
    }
    return a + fsum_indirect1__mm5(a - 1);
}

// Extended return type forward declaration
unsigned long long fulonglong(int a);
unsigned long long fulonglong(int a) {
    return a;
}

// No parameter name and extended parameter type forward declaration
int fforward_noparamname(unsigned int, int b);
int fforward_noparamname(unsigned int a, int b) {
    return a;
}

// XXX Missing function pointer types

// XXX Missing varargs
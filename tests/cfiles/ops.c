/**
 * Some hand-written operation tests
 */

float fconst() {
    return 3.14f;
}

float fadd(float a, float b) {
    return a + b;
}

float fmul(float a, float b) {
    return a * b;
}

// Two mismatches expected, epycc generates a phi select node, clang generates
// or and epycc a select instruction
float flor__mm2(float a, float b) {
    return a || b;
}

float fdouble(float a) {
    return 2.0f * a;
}

float fgte(float a, float b) {
    return (a >= b);
}

float f2pow2(int a) {
    return 2.0f * (a * a);
}

signed char fcast(int a) {
    return (unsigned int) a;
}

float fpp(float a) {
    return a++;
}

float fmm(float a) {
    return a--;
}
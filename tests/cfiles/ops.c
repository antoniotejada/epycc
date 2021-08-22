/**
 * Some hand-written operation tests
 */

float fconst() {
    return 3.14f;
}

float fadd(float a, float b) {
    return a + b;
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
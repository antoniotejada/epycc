/*
 * Constant expression tests
 */

// Float numeric constants
double float_constants() {
    return 
        1.2 + 1.2f + 1.2F + 
        // dot number
        .1  + .1f + .1F + .1l + .1L +
        // number dot
        1. + 1.f + 1.F + 1.l + 1.L +
        // Inexact constants
        3.1415f + 3.1415l + 
        // scientific notation
        1e2 + 1e2f + 1e2F + 1e2l + 1e2L +
        1.2e2 + 1.2e2f + 1.2e2F + 1.2e2l + 1.2e2L +
        .0e2 + .1e2f + .1e2F + .0e2l + 1.0e2L +
        1e+2 + 1e+2f + 1e+2F + 1e+2l + 1e+2L +
        1e-2 + 1e-2f + 1e-2F + 1e-2l + 1e-2L
    ;
}

// Test that returning a straight constant with no operations involved is ok
int single_constant() {
    return 5;
}

// Integer numeric constants
int int_constants() {
    return 
        25 + 
        25L + 25U + 25l + 25u 
        // XXX Looks like Larks' lexer regexps are acting up and the second
        //     suffix is taken as identifier, uncomment once it's fixed
        /* 
        25LL + 25LU + 25LLU + 25UL + 25ULL + 
        25ll + 25lu + 25llu + 25ul + 25ull +
        25Lu + 25LLu + 25uL + 25uLL +
        25lU + 25llU + 25Ul + 25Ull 
        */
       // XXX Also missing hex, oct, etc
    ;
}

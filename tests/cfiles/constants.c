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
        1e-2 + 1e-2f + 1e-2F + 1e-2l + 1e-2L +
        // hexadecimal floating constants
        0x1.999999999999ap-4 +
        0x3.3333333333334p-5 +
        0xcc.ccccccccccdp-11 +
        0x0.3p10f + 0x0.3p10F + 0x0.3p10l + 0x0.3p10L
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
        25L + 25U + 25l + 25u +
        // XXX Looks like Larks' lexer regexps are acting up and the second
        //     suffix is taken as identifier, uncomment once it's fixed
        25LL + 25LU + 25LLU + 25UL + 25ULL + 
        25ll + 25lu + 25llu + 25ul + 25ull +
        25Lu + 25LLu + 25uL + 25uLL +
        25lU + 25llU + 25Ul + 25Ull +
        // Hex
        0x25 + 
        0x25L + 0x25U + 0x25l + 0x25u +
        0x25LL + 0x25LU + 0x25LLU + 0x25UL + 0x25ULL + 
        0x25ll + 0x25lu + 0x25llu + 0x25ul + 0x25ull +
        0x25Lu + 0x25LLu + 0x25uL + 0x25uLL +
        0x25lU + 0x25llU + 0x25Ul + 0x25Ull +
        0X25 + 
        0X25L + 0X25U + 0X25l + 0X25u +
        0X25LL + 0X25LU + 0X25LLU + 0X25UL + 0X25ULL + 
        0X25ll + 0X25lu + 0X25llu + 0X25ul + 0X25ull +
        0X25Lu + 0X25LLu + 0X25uL + 0X25uLL +
        0X25lU + 0X25llU + 0X25Ul + 0X25Ull +
        // Oct
        025 + 
        025L + 025U + 025l + 025u +
        025LL + 025LU + 025LLU + 025UL + 025ULL + 
        025ll + 025lu + 025llu + 025ul + 025ull +
        025Lu + 025LLu + 025uL + 025uLL +
        025lU + 025llU + 025Ul + 025Ull
        
    ;
}

/**
 * Assign operator tests
 */
float arith_ops(int a, int b) {
    b = 5.0;
    a += 1;
    b -= 2;
    a *= 2.0;
    b %= 5;
    a /= 3;

    return a + b;
}

float bitwise_ops__mm1(unsigned int a, unsigned int b) {
    a &= 65535;
    b |= 255;
    a ^= 1;

    // Expected 1 bening mismatch here due to operand order
    return a + b;
}

float shift_ops(unsigned int a, unsigned int b) {
    a >>= 1;
    b <<= 4;
    return a + b;
}
int f(int a, int b) {
    int c;
    int d, e; 
    int f=4, g;
    int h, i=7;
    int j=8, k=9;

    c = 1; d = 2; e = 3; g = 5; h = 6;

    return a + b + c + d + e + f + g + h + i + j + k;
}

// Test specifier orderings (note C doesn't accept duplicated specifiers, so no
// need to test that)
int unsigned f_orderings(unsigned int a, int unsigned b, signed int c, int signed d) {
    unsigned u = 0;
    signed s = 0;
    char signed cs = 0;
    signed char sc = 0;
    unsigned char uc = 0;
    char unsigned cu = 0;
    unsigned int ui = 0;
    int unsigned iu = 1;
    int signed is = 0;
    signed int si = 0;
    unsigned long ul = 0;
    long unsigned lu = 0;
    signed long sl = 0;
    long signed ls = 0;
    unsigned long long ull= 0;
    long unsigned long lul = 0;
    long long unsigned llu = 0;
    signed long long sll = 0;
    long signed long lsl = 0;
    long long signed lls = 0;
    long double ld = 0;
    double long dl = 0;

    return c;
}

// XXX Missing qualifiers const, etc
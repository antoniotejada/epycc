// Note structs can't be empty, so no ned to test that
int fstruct(int a, int b) {
    struct {
        float f;
        int i1, i2;
        unsigned int u1;
        int unsigned u2;
        unsigned u3;
    } s;
    s.f = a;
    s.i1 = a;
    s.i2 = a;
    s.u1 = a;
    s.u2 = a;
    
    
    return s.f;
}

int fstruct_nested(int a, int b) {
    struct {
        float f;
        int i1, i2;
        struct {
            unsigned int u1;
            unsigned int u2;
        } t;
    } s;
    s.t.u1 = a;
    
    return s.t.u1;

}

// XXX Test structs inside control flow
// XXX const struct, etc
// XXX Test struct parameter

// XXX Test returning struct
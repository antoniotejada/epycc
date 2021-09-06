// Note C doesn't allow arrays inside structs to be dynamic
int fstruct_of_array(int a, int b) {
    struct {
        float f;
        int i1, i2;
        int arr[10];
    } s;
    s.arr[1] = 1.0f;
    
    return s.arr[1];
}

int farray_of_struct(int a, int b) {
    struct {
        float f;
        int i1, i2;
    } s[2];
    s[1].f = 1.0f;
    
    return s[1].f;
}

// XXX Test struct pointer parameter
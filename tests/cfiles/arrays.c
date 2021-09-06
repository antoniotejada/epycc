// Compile time-sized sigle-dimensional array
int farray_1d_fixed(int a) {
    int b[3][5];
    b[2][1] = 1;
    return b[2][1];
}

// Compile time-sized multidimensional array
int farray_2d_fixed(int a) {
    int b[3][5];
    b[2][1] = 1;
    return b[2][1];
}

// Runtime-sized multidimensional array
// 12 mismatches due to clang doing single exit block and cleanup for stackrestore
int farray_2d_dynamic(int a, int b) {
    int c[a][b]; 
    c[1][2] = b;
    
    return c[1][2];
}

// Hybrid runtime and compile time multidimensional sized array
int farray_2d_fixed_and_dynamic(int a) {
    int b[3][a];
    b[2][1] = 1;
    return b[2][1];
}


// Dynamic array inside an if block
// 12 mismatches due to clang doing single exit block and cleanup for stackrestore
int fifarray_2d_dynamic__mm12(int a) {
    int s = 0;
    if (a > 10) {
        int b[3][a];
        b[2][1] = 1;
        s = b[2][a];
    }   
    return s;
}


// Check that dynamic arrays are released properly when abreak/continue is 
// at the same scope as the dynamic array
int fforarray_1d_break_dynamic(int a) {
    for (int i = 0; i < a; ++i) {
        int arr[a];
        // same scope break
        if (a > 1000) break;
        arr[a] = a;
    }
    return a;
}

// 4 mismatches due to clang doing single exit block for stackrestore
int fforarray_1d_dynamic__mm4(int a) {
    int s = 0;
    for (int i = 0; i < a; ++i) {
        int arr[a];
        arr[1] = 0;
        // Same scope return and break
        if (a > 5000) return 0;
        if (a > 100) break;
        arr[2] = a;
        arr[i] = arr[i-1] + 2;
        s = arr[i];
    }
    return s;
}

// 4 mismatches due to clang doing single exit block for stackrestore
int fforarray_1d_dynamic_nested__mm4(int a) {
    int s = 0;
    for (int i = 0; i < a; ++i) {
        int arr[a];
        arr[1] = 0;
        if (a > 5000) return 0;
        if (a > 1000) break;
        arr[2] = a;
        arr[i] = arr[i-1] + 2;
        s = arr[i];
        for (int j = 0; j < s; ++j) {
            int brr[a];
            brr[1] = i;
            if (s > 750) return 0;
            if (s > 500) continue;
            brr[i] = brr[j-1] + 2;
        }
    }
    return s;
}


// Function parameter single dimensional array
int farray_1d_params(int a[10], int b) {
    a[5] = b;
    
    return a[5];
}


// Function parameter 2-dimensional array
int farray_2d_params(int a[10][5], int b) {
    a[5][2] = b;
    
    return a[5][2];
}

// Function parameter 3-dimensional array
int farray_3d_params(int a[10][5][2], int b) {
    a[5][2][1] = b;
    
    return a[5][2][1];
}

// Testing destructuring fixed size 2d array into 1d array
int farray_2d_to_1d(int a[10][5], int b[5]) {
    if (b[0] != 1) {
        a[0][0] = 1;
        farray_2d_to_1d(a, a[1]);
    }
    return a[1][0];
}

// Testing destructuring open 2d array into 1d array
int farray_3d_to_1d(int a[][5], int b[5]) {
    if (b[0] != 1) {
        a[0][0] = 1;
        farray_2d_to_1d(a, a[1]);
    }
    return a[1][0];
}


// XXX Test arrays of chars
// XXX Test arrays of arrays
// XXX Test function parameter arrays variable sized via global vars (needs global support)
// XXX Test function parameter arrays open dimensions


// XXX Test constant sized arrays initialized (needs global support)
// XXX Test constant sized arrays open last dimension 
// XXX Test several variable sized arrays, in the same scope, there should only be one stacksave/restore
// XXX Test several variable sized arrays, in different serial scopes
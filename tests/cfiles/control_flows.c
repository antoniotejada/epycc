// for/if then combinations
int fifforf(int a, int b) {
    int s = 0;
    if (a > b) {
        for (int i = 0; i < a; i += 1) {
            s += b;
        }
    } else {
        for (int i = 0; i < b; i+= 1) {
            s += a;
        }
    }
    return s;
}

int fforif(int a, int b) {
    int s = 0;
    for (int i = 0; i < a; i += 1) {
        if (a > b) {
            s += b;
        } else {
            s += a;
        }
    }
    
    return s;
}
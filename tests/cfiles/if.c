// This uses spec select 
int fif(int a) {
    if (a == 1)
        a = 0;
    
    if (a == 2) {
        a = 1;
    }

    return a;
}

int felse(int a, int b) {
    if (b == 2) {
        a = 0;
    } else {
        a = 1;
    }

    if (b == 2) {
        a = 0;
    } else
        a = 1;

    if (b == 2)
        a = 0;
    else {
        a = 1;
    }

    if (b == 2)
        a = 0;
    else
        a = 1;

    return a;
}

// Test dangling else
int felse_dangling(int a, int b) {
    if (a == 2)
        if (b == 1) 
            b = 0;
        else b = 1; 
    else
        b = 3;

    return a;

}


// Test that allocas for parameters and local variables are not done locally to
// a block (there used to be a bug where the alloca was done inside the "if"
// block and then tried to use from the "then" block, which llvm would fail to
// compile because "instruction does not dominate all uses").
int fif_param(int a, int b) {
    if (a == 0) {
        b = 1;
    } else {
        b = 2;
    }
    return b;
}
int fif_local(int a) {
    int b;
    if (a == 1) {
        b = 1;
    } else {
        b = 2;
    }
    return b;
}
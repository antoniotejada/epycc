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

    return b;

}

// test chained if/else 
int fif_chained(int a, int b) {
    if (a == 1) {
        b = 0;
    } else if (b == 2) {
        b = 5;
    } else {
        b = 6;
    }
    return b;
}

// returning from chain without end return used to exhibit a bug where 
// the block was left unterminated
int fif_chainedreturn(int a, int b) {
    if (a == 1) {
        return 0;
    } else if (b == 2) {
        return 5;
    } else {
        return 6;
    }
}

// test nested if/else
int fif_nested(int a, int b) {
    if (a == 1) {
        if (b == 2) {
            b = 5;
        } else {
            b = 6;
        }
    } else {
        if (b == 5) {
            b = 8;
        } else {
            b = 7;
        }
    }
    return b;
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

int fif_return(int a) {
    int b;
    if (a == 1) {
        return 1;
    } else {
        return 2;
    }
    return b;
}

// Test that the initialization expression of variables remains in the disjoint
// block even if the alloca is bubbled up to the common code
int fif_bbinit(int a, int b) {
    if (a == 1) {
        int c = a * b;
        b = c;
    } else {
        int c = a + b;
        b = c;
    }
    return b;
}
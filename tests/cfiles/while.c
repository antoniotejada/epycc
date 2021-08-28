int fwhile(int a, int b) {
    int s = 0;
    while (a > b) {
        s += a;
    }

    return s;
}

int fwhile_empty(int a, int b) {
    int s = 0;
    while ((s += a) > b) {

    }
    return s;
}

int fwhile_break(int a, int b) {
    int s = 0;
    while (a > b) {
        if (s > 1000) {
            break;
        }
        s += a;
    }

    return s;
}

int fwhile_continue(int a, int b) {
    int s = 0;
    while (a > b) {
        s += b;
        if ((s % 5) == 0) {
            continue;
        }
        s += a;
    }

    return s;
}

int fwhile_return(int a, int b) {
    int s = 0;
    while (a > b) {
        if (s > 1000) {
            return s;
        }
        s += a;
    }

    return s;
}

// Test that we silently ignore branches on terminated basic blocks (the if condition
// will try to branch but that basic block already has a break branch)
int fwhile_terminated_cbranch(int a, int b) {
    while (1) {
        break;
        if (a == 1) {

        } 
    }
    return b;
}

// Test that we silently ignore branches on terminated basic blocks (the if/then
// will try to branch to the end of the if but it has already been terminated
// by the break)
int fwhile_terminated_branch(int a, int b) {
    while (1) {
        if (a == 1) {
            break;
        } 
    }
    return b;
}

// XXX Nest whiles

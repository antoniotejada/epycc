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

// XXX Nest whiles
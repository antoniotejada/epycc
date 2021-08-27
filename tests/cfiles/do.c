int fdo(int a, int b) {
    int s = 0;
    do {
        s += a;
    } while (a > b);

    return s;
}

int do_empty(int a, int b) {
    int s = 0;
    do {

    } while ((s += a) > b);
    return s;
}

int fdo_break(int a, int b) {
    int s = 0;
    do {
        if (s > 1000) {
            break;
        }
        s += a;
    } while (a > b);

    return s;
}

int fdo_continue(int a, int b) {
    int s = 0;
    do {
        s += b;
        if ((s % 5) == 0) {
            continue;
        }
        s += a;
    } while (a > b);

    return s;
}

int fdo_return(int a, int b) {
    int s = 0;
    do {
        if (s > 1000) {
            return s;
        }
        s += a;
    } while (a > b);

    return s;
}

// XXX Nest dos
float ffor(int a) {
    int s = 0;
    for (int i = 0; i < a; i += 4) {
        s += i;
    }
    return s;
}

float ffor_postincr(int a) {
    int s = 0;
    for (int i = 0; i < a; i++) {
        s += i;
    }
    return s;
}

float ffor_preincr(int a) {
    int s = 0;
    for (int i = 0; i < a; ++i) {
        s += i;
    }
    return s;
}

float ffor_noblock(int a) {
    int s = 0;
    for (int i = 0; i < 10; i += 4)
        s += i;
    return s;
}

float ffor_nobody(int a) {
    int s = 0;
    for (int i = 0; i < 10; i += 4, s += 8);

    return s;
}

float ffor_nodecl(int a) {
    int s = 0;
    int i = 0;
    for (; i < 10; i += 4) {
        s += i;
    }
    return s;
}

float ffor_noincr(int a) {
    int s = 0;

    for (int i = 0; i < 10; ) {
        s += i++;
    }

    return s;
}

// XXX test returns inside for

float ffor_nocond(int a) {
    int s = 0;
    for (int i = 0; ; i++) {
        if (i > 10) {
            break;
        }
        s += i;
    }

    return s;
}

float ffor_continue(int a) {
    int s = 0;
    for (int i = 0; ; i++) {
        if ((i % 2) == 0) {
            continue;
        }
        s += i;
    }

    return s;
}


float ffor_decl(int a) {
    int s = 0;
    int i = 0;
    // test that the loop declaration can be hidden (note cannot declare without
    // a block, son no need to test without a block)
    for (int i = 0; i < 10; i += 4) {
        int i = 0;
        s += i;
    }
    return s;
}

float ffor_nested(int a, int b) {
    int s = 0;
    for (int i = 0; i < a; i += 4) {
        for (int j = 0; j < i; j += 8) {
            s += i * j;
        }
        s += i;
    }
    return s;
}
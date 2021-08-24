// Test that parameters can be redifined inside a block
int f(int a) {
    // XXX This should do the negative test that redefining a function parameter
    //     gives an error, once we support errors and expect errors in tests
    a = 1;
    float b = 0;
    {
        float a = 50;
        {
            int a = 25;
            b = a + b;
        }
        b = a + b;
    }
    b = a + b;

    return b;
}

// Test that the empty function and returning void is allowed
void empty() {
}

// Test that empty scopes are allowed
void empty_scope() {
    {
    }
}
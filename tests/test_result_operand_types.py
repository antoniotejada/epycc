#!/usr/bin/env python
"""
Test all combinations of result and operand types for a single arithmetic
operation.

There are a few expected mismatches wrt clang, all benign.
"""
import itertools
import os
import sys

# Add the parent dir to syspath to be able to import epycc
epycc_dirpath = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(epycc_dirpath)
import epycc

import test_cfiles

# Some of the tests have benign mismatches, decorate the function names with the
# expected number of mismatches so test_single_cfile doesn't assert when it
# finds them
expected_mismatch_counts = {
    "test_add___Bool__char___Bool" : 4,
    "test_add___Bool__unsigned_char__short" : 2,
    "test_add___Bool__long__int" : 2,
    "test_add___Bool___Bool___Bool" : 2,
    "test_add___Bool__unsigned_char__short" : 2,
    "test_add___Bool__long__int" : 2,
    "test_add___Bool___Bool___Bool" : 2,
    "test_add___Bool__unsigned_long__long" : 2,
    "test_add___Bool__unsigned_short__unsigned_short" : 2,
    "test_add___Bool__short__short" : 2,
    "test_add___Bool__long__long" : 2,
    "test_add___Bool__unsigned_short__char" : 2,
    "test_add___Bool__unsigned_long__unsigned_long" : 2,
    "test_add___Bool__unsigned_long_long__unsigned_long_long" : 2,
    "test_add___Bool__char__unsigned_char" : 2,
    "test_add___Bool__unsigned_short__unsigned_char" : 2,
    "test_add___Bool__unsigned_int__unsigned_int" : 2,
    "test_add___Bool__short___Bool" : 4,
    "test_add___Bool__unsigned_int__long" : 2,
    "test_add___Bool__unsigned_char__unsigned_char" : 2,
    "test_add___Bool__unsigned_short__short" : 2,
    "test_add___Bool__unsigned_long_long__long_long" : 2,
    "test_add___Bool__unsigned_char___Bool" : 4,
    "test_add___Bool__int__int" : 2,
    "test_add___Bool__char__char" : 2,
    "test_add___Bool__char__short" : 2,
    "test_add___Bool__long_long__long_long" : 2,
    "test_add___Bool__unsigned_int__unsigned_long" : 2,
    "test_add___Bool__unsigned_int__int" : 2,
    "test_add___Bool__unsigned_short___Bool" : 4,
    "test_add___Bool__unsigned_long__int" : 2,
}

out_dir = os.path.join(epycc_dirpath, "_out")

test_filename = "all_result_operand_types.c"
test_filepath = os.path.join(out_dir, test_filename)

ignore_existing_files = False

# If the C file already exists, no need to generate the test's C file
if (ignore_existing_files or (not os.path.exists(test_filepath)) or 
    (os.path.getmtime(test_filepath) <= os.path.getmtime(__file__))):

    # Don't pick the redundant "signed xxxxx" types
    type_list = list(epycc.unsigned_integer_types | epycc.unspecified_integer_types)
    l = []
    # Test one operation with the cross product of result and operand types
    for (binop_sign, binop_name), res_type, a_type, b_type in itertools.product(
        epycc.binops[:1], type_list, type_list, type_list):

        # Assume operation is commutative and skip half the cross product
        if (type_list.index(b_type) > type_list.index(a_type)):
            continue

        # Don't do integer-only operations (bitwise, mod) on non-integer
        # operands
        if ((binop_sign in epycc.int_ops) and 
            ((a_type not in epycc.integer_types) or (b_type not in epycc.integer_types))):
            continue

        # char add__char__char__char(char a, char b) { return (char) (a + b); }
        fn_name = "test_%s" % epycc.get_fn_name(binop_name, res_type, a_type, b_type)
        fn = "%s %s__mm%d(%s a, %s b) { return a %s b; }" % (
            res_type, 
            fn_name,
            expected_mismatch_counts.get(fn_name, 0),
            a_type, b_type,
            binop_sign,
        )

        l.append(fn + "\n")
    
    with open(test_filepath, "w") as f:
        f.writelines(l)

mismatch_count = test_cfiles.test_single_cfile(test_filepath, ignore_existing_files) 

# This has 28 differences wrt clang mainly due to parameter ordering when using
# _Bool. There are also some other differences that look benign.
# XXX This should be removed if the epycc-generated IR is used as gold?
assert(mismatch_count == 28)
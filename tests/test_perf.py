#!/usr/bin/env python
"""
Copyright (C) 2021 Antonio Tejada

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.


See
http://tungwaiyip.info/blog/2009/07/16/ctype_performance_benchmark
https://stackoverflow.com/questions/51384157/cprofile-adds-significant-overhead-when-calling-numba-jit-functions
https://github.com/numba/numba/blob/0.39.0/numba/_dispatcher.c#L490-L585
https://github.com/numba/numba/blob/0.39.0/numba/_dispatcher.c#L286-L344
"""

import ctypes
import functools
import gc
import os
import string
import sys
import time

# numba initializes llvmlite when numba is first imported, set any llvmlite
# options before numba is imported
# (some llvm options can be changed after the fact, but eg asm syntax is not one
# of them)
# llvm options accept single or double dash indistinctly
import llvmlite.binding as llvm
#llvm.set_option('', '--x86-asm-syntax=intel')

#os.environ['NUMBA_ENABLE_AVX'] = "0"
#os.environ['NUMBA_NUM_THREADS'] = "1"
#os.environ['NUMBA_DUMP_ASSEMBLY'] = "1"
#os.environ['NUMBA_DUMP_OPTIMIZED'] = "1"
# XXX Enabling this disables dumping assembly, there's no corresponding config 
#     option
#os.environ['NUMBA_DISABLE_ERROR_MESSAGE_HIGHLIGHTING'] = "1"
#os.environ['NUMBA_DEBUG_TYPEINFER'] = "1"


import numpy as np
import numba as nb
#nb.config.DUMP_ASSEMBLY = 1 
#nb.config.DUMP_OPTIMIZED = 1
#nb.config.ANNOTATE = 1
#nb.config.HTML="numbatest.html"

import pyrr

# Add the parent dir to syspath to be able to import epycc
epycc_dirpath = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(epycc_dirpath)
import epycc

def get_numba_elf(fn):
    # See https://stackoverflow.com/questions/42138764/marshaling-object-code-for-a-numba-function
    # can get assembler via
    # objdump.exe -d nb_transform_vectors.elf > nb_transform_vectors.s
    cres = fn.overloads.values()[0]  # 0: first and only type signature
    elfbytes = cres.library._compiled_object

    return elfbytes

def tuplize(lst, count = None):
    """
    Convert any non-tuple aggregate (ctypes pointer, python list, numpy array,
    etc) of float-ish (float, np.float32, ctypes.c_float) to tuple
    """
    if (count is None):
        count = len(lst)
    
    # This has to be safe wrt:
    # - ctypes pointers which don't have len and don't have iters, so they need
    #   to be iterated via index and knowing the count
    # - np.float32 which have __getitem__ so could be confused with lists if
    #   checked against __getitem__ in order to call tuplize() again
    return tuple(
        tuplize(lst[i]) if not isinstance(lst[i], (float, np.float32, ctypes.c_float)) 
        else lst[i] for i in xrange(count))

def nblistize(lst):
    nblist = nb.typed.List()
    for i in lst:
        if (isinstance(i, list)):
            nblist.append(nblistize(i))
        else:
            nblist.append(i)
    return nblist

s = """
float dot_prod_vector(float v[4], float w[4])
{
    float res = 0.0f;
    for (int i = 0; i < 4; ++i)
    {
        res += v[i] * w[i];
    }
    return res;
}

void transform_vector(float mat[4][4], float in[4], float out[4])
{
    for (int i = 0; i < 4; ++i)
    {
        out[i] = dot_prod_vector(mat[i], in);
    }

}

void transform_vectors(float matrix[4][4], float in[][4], float out[][4], int count) {
    for (int i = 0; i < count; ++i) 
    {
        transform_vector(matrix, in[i], out[i]);
    }
}

void transform_vectors_empty(float matrix[4][4], float in[][4], float out[][4], int count) {
    return;
}

void empty() {
}
"""

def dot_prod(v, w):
    return sum([vi * wi for vi, wi in zip(v, w)])
    
def transform_vector(mat, v):
    return [dot_prod(row, v) for row in mat]

def transform_vectors(mat, vs, vsout):
    vsout[:] = [transform_vector(mat, v) for v in vs]

def pyrr_transform_vectors(pmat, pvs, pvsout):
    # XXX pyrr apply_to_vector can't take list of vectors despite the docs
    #    saying so, need to do it manually in Python
    #    see https://github.com/adamlwgriffiths/Pyrr/issues/106
    for i in xrange(len(pvs)):
        pvsout[i] = pyrr.matrix44.apply_to_vector(pmat, pvs[i])

@nb.njit
def nb_dot_prod(v, w):
    # Numba cannot determine the type of sum, switch to loop
    # return np.sum([vi * wi for vi, wi in zip(v, w)])

    # This needs to be specified as float32 so the result is accumulated in 
    # float32, even if the arguments are already float32
    res = np.float32(0)
    for vi, wi in zip(v, w):
        res += vi * wi

    return res

@nb.njit
def nb_transform_vector(mat, v, vout):
    # Numba errors out with
    # "Direct iteration is not supported for arrays with dimension > 1"
    # Need to use array indexing instead of element traversing
    for i in xrange(len(mat)):
        vout[i] = nb_dot_prod(mat[i], v)


@nb.njit
def nb_transform_vectors(mat, vs, vsout):
    # Numba errors out with
    # "Direct iteration is not supported for arrays with dimension > 1"
    # Need to use array indexing instead of element traversing
    # To get really good performance, don't let numba generate python lists and
    # pass arrays for source and destination
    for i in xrange(len(vs)):
        nb_transform_vector(mat, vs[i], vsout[i])

@nb.njit
def nb_transform_vectors_empty(mat, vs, vsout):
    pass

@nb.njit
def nb_empty():
    pass

def nested_list_to_ctype_array(l, item_ctype):
        def get_ctype(l):
            if (isinstance(l, (tuple, list))):
                return get_ctype(l[0]) * len(l)
            else:
                return item_ctype

        def deep_copy(l, arr):
            if (isinstance(l[0], (tuple, list))):
                for i in xrange(len(l)):
                    deep_copy(l[i], arr[i])
            else:
                for i in xrange(len(l)):
                    arr[i] = l[i]
        
        list_ctype = get_ctype(l)
        arr = list_ctype()
        deep_copy(l, arr)

        return arr

def epycc_transform_vectors_numpy(cfunc, npmatrix, npvectors, npvectors_out, count):
    cmatrix = npmatrix.ctypes.data_as(cfunc.argtypes[0])
    cvectors = npvectors.ctypes.data_as(cfunc.argtypes[1])
    cvectors_out = npvectors_out.ctypes.data_as(cfunc.argtypes[2])
    ccount = cfunc.argtypes[3](i)
    
    cfunc(cmatrix, cvectors, cvectors_out, ccount)


count = 1000
#count = 5
step = 50
# Use small counts to do a single count visual validation of the result to verify
# results are calculated properly, otherwise do performance testing
do_validation_only = (count <= 10)

vectors = [
    [1.0 * _, 2.0 * _, 3.0 * _, 4.0 * _ ] for _ in xrange(count)
]

vectors_out = [
    [5.0, 6.0, 7.0, 8.0 ] for _ in xrange(count)
]

matrix = [
    [ 0.0, 1.0, 0.0, 0.0 ],
    [ 1.0, 0.0, 0.0, 0.0 ],
    [ 0.0, 0.0, 1.0, 0.0 ],
    [ 0.0, 0.0, 0.0, 1.0 ],
]


def python_empty():
    pass

def timeit(fn, args = tuple(), reps = 5):
    times = []
    gc_enabled = gc.isenabled()
    
    # Warm up the function in case it's jitted, etc
    fn(*args)

    num_calls = 10
    # Python exec needs the locals as a new dict if they are going to be
    # modified, make a copy
    d = dict(locals())
    g = globals()
    calibrate = False
    while (True):
        code = """
t = time.clock
start_time = t()
%s
elapsed_time = t()
elapsed_time = elapsed_time - start_time
        """ % (string.join(["fn(*args)"] * num_calls, ";"))        
        ast = compile(code, os.path.basename(__file__), 'exec')
        # Add function calls until the elapsed time is over a threshold
        elapsed_time = None
        
        if (not calibrate):
            break
        
        gc.disable()
        exec(ast, g, d)
        if (gc_enabled):
            gc.enable()
        elapsed_time = d['elapsed_time']
        #print elapsed_time, num_calls
        # time.clock() accuracy is a bit better than 1us, from 9.33e-7s
        # to 4.66e-7s on this machine (depending on cpu frequency)
        if (elapsed_time > 1e-6):
            break
        num_calls *= 2

    if (calibrate):
        # Store the calibration result
        times.append(elapsed_time / num_calls)

    # Iterate 
    gc.disable()
    for _ in xrange(reps - len(times)):
        exec(ast, g, d)
        elapsed_time = d['elapsed_time']
        times.append(elapsed_time / num_calls)

    if (gc_enabled):
        gc.enable()

    return times

lib = epycc.epycc_compile(s)

start = 1
if (do_validation_only):
    start = count

for i in xrange(start, count + 1, step):
    npmatrix = np.array(matrix, np.float32)
    npvectors = np.array(vectors[:i], np.float32)
    npvectors_out = np.array(vectors_out[:i], np.float32)

    lmatrix = npmatrix.tolist()
    lvectors = npvectors.tolist()
    lvectors_out = npvectors_out.tolist()

    pmatrix = pyrr.Matrix44(matrix)
    pvectors = [pyrr.Vector4([_ for _ in v]) for v in vectors[:i]]
    pvectors_out = [pyrr.Vector4([_ for _ in v]) for v in vectors_out[:i]]

    cfunc = lib.__raw_transform_vectors_empty
    cmatrix = npmatrix.ctypes.data_as(cfunc.argtypes[0])
    cvectors = npvectors.ctypes.data_as(cfunc.argtypes[1])
    cvectors_out = npvectors_out.ctypes.data_as(cfunc.argtypes[2])
    ccount = cfunc.argtypes[3](i)

    # Numba deprecates reflected lists so the closest to not using numpy
    # parameters is using numba.typed.list or tuples
    tmatrix = tuplize(npmatrix.tolist())
    tvectors = tuplize(npvectors.tolist())
    # Output parameter can't be tuple since it can't be modified, use a numba
    # typed list instead
    nbvectors_out = nblistize(npvectors_out.tolist())
    
    l = [str(i)]
    header = ["params"]

    # Note timeit takes care of doing a dummy call before measuring so
    # numba's jit is warmed up
    for name, fn, args, res in [
        ("numba empty no params", nb_empty, [], None),
        ("python empty no params", python_empty, [], None),
        ("epycc empty no params", lib.empty, [], None),

        ("numba tuple params only", nb_transform_vectors_empty, (tmatrix, tvectors, nbvectors_out), None), 
        ("epycc tuple params only", lib.transform_vectors_empty, (tmatrix, tvectors, lvectors_out, i), None),
        ("epycc list params only", lib.transform_vectors_empty, (lmatrix, lvectors, lvectors_out, i), None),
        
        ("numba numpy params only", nb_transform_vectors_empty, (npmatrix, npvectors, npvectors_out), None), 
        ("epycc numpy params only", epycc_transform_vectors_numpy, (lib.__raw_transform_vectors_empty, npmatrix, npvectors, npvectors_out, count), None),
        ("epycc ctypes params only", lib.__raw_transform_vectors_empty, (cmatrix, cvectors, cvectors_out, ccount), None),

        ("numba tuple", nb_transform_vectors, (tmatrix, tvectors, nbvectors_out), nbvectors_out), 
        ("epycc tuple", lib.transform_vectors, (tmatrix, tvectors, lvectors_out, i), lvectors_out),
        ("epycc list", lib.transform_vectors, (lmatrix, lvectors, lvectors_out, i), lvectors_out),
        
        ("numba numpy", nb_transform_vectors, (npmatrix, npvectors, npvectors_out), npvectors_out ), 
        ("epycc numpy", epycc_transform_vectors_numpy, (lib.__raw_transform_vectors, npmatrix, npvectors, npvectors_out, count), npvectors_out),
        ("epycc ctypes", lib.__raw_transform_vectors, (cmatrix, cvectors, cvectors_out, ccount), cvectors_out),
        ("numpy", np.dot, (npvectors, npmatrix.T, npvectors_out), npvectors_out),
        ("python", transform_vectors, (lmatrix, lvectors, lvectors_out), lvectors_out),
        ("pyrr", pyrr_transform_vectors, (pmatrix, pvectors, pvectors_out), pvectors_out),
    ]:    

        if (do_validation_only):
            if (res is not None):
                # Initialize all outs because they may be shared across fns and
                # the result could carry over hiding if this fn doesn't work
                # XXX There should be a better way of doing this than 
                #     replicating the initialization above for every fn
                npvectors_out = np.array(vectors_out[:i], np.float32)
                lvectors_out = npvectors_out.tolist()
                pvectors_out = [pyrr.Vector4([_ for _ in v]) for v in vectors[:i]]
                cvectors_out = npvectors_out.ctypes.data_as(cfunc.argtypes[2])

                fn(*args)
                print name
                if (fn == epycc_transform_vectors_numpy):
                    print tuplize(args[2], count)

                elif (fn == np.dot):
                    print tuplize(args[0], count)

                else:
                    print tuplize(args[1], count)
                    
                print tuplize(res, count)

        else:
            if (i == 1):
                header.append(name)
        
            times = timeit(fn, args)
            # pick the lowest time
            l.append("%f" % (min(times) * 1e6))

    if (not do_validation_only):
        if (i == 1):
            print string.join(header, ",")
            
        print string.join(l, ",")
    sys.stdout.flush()


# XXX Test numba vectorize
# XXX Use numba's CPUDispatcher? see https://stackoverflow.com/questions/51384157/cprofile-adds-significant-overhead-when-calling-numba-jit-functions
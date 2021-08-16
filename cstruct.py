#!/usr/bin/env python
import string

class ClassStruct:
    """
    Memory lightweight class
    """
    __slots__ = ["field1", "field2"]
    def __init__(self, field1, field2):
        self.field1 = field1
        self.field2 = field2

class Struct:
    """
    C-like struct

    use with 
    my_struct = Struct(field1=value1, field2=value2)
    my_struct.field1 = blah

    new fields can also be added after the fact with
    my_struct.field3 = blah
    """
    def __init__(self, **kwds):
        self.__dict__.update(kwds)
    def __repr__(self):
        # XXX This may be making the debugger stall when there are lots of 
        #     linked states?
        s = []
        for key in self.__dict__:
            s += ["%s: %s" % (key, self.__dict__[key])]
        return "{" + string.join(s, ", ") + "}"

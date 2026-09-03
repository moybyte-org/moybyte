// Enough of MicroPython's object model for the two meter tuples. The stubs
// REMEMBER whether a value crossed as signed or unsigned, because
// pump_stats()'s feed entry is the one that must stay signed: feed_us is -1
// until a frame has fed its last band, and mp_obj_new_int_from_uint(-1) would
// hand the PUMP line 4294967295 instead.
#ifndef H_STUB_PY_OBJ_H
#define H_STUB_PY_OBJ_H

#include <stdbool.h>
#include <stddef.h>

#include "harness.h"

typedef struct h_mp_obj *mp_obj_t;

mp_obj_t mp_obj_new_int(long long v);
mp_obj_t mp_obj_new_int_from_uint(unsigned long long v);

#define MP_OBJ_NEW_SMALL_INT(v) mp_obj_new_int((long long)(v))

#endif // H_STUB_PY_OBJ_H

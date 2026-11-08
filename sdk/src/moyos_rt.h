// The module's runtime (sdk/README.md): the OS table the crt keeps, which every
// stub in moyos_stubs.c calls through.
#ifndef MOYOS_RT_H
#define MOYOS_RT_H

#include "moybyte/os.h"

extern const moy_os_table_t *__moy_os __attribute__((visibility("hidden")));

#endif

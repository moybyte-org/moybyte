// The runtime map (docs/kernel_cartpath_2026-10.md §3.3): which cart runtimes
// this image runs, by the manifest's "runtime". A runtime the image lacks is
// an absent row. A row whose ops call into the VM says so (`vm`), and no
// VM-free run takes it.

#ifndef MOY_RT_H
#define MOY_RT_H

#include <stdbool.h>

typedef struct {
    const char *name;           // the manifest's "runtime"
    bool vm;                    // its ops call into the VM
} moy_rt_ops_t;

#define MOY_RT_ROWS 4

// A row added, or the row of that name replaced: 0, or -1 when the map is full.
int moy_rt_add(const moy_rt_ops_t *ops);
// The row of that name withdrawn (the Python row, at a VM's stop).
void moy_rt_remove(const char *name);
// NULL: not in this image.
const moy_rt_ops_t *moy_rt_get(const char *name);
const moy_rt_ops_t *moy_rt_get_n(const char *name, unsigned n);

#endif // MOY_RT_H

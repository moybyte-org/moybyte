// The spine's allocator on the host, over calloc, and the few table calls the
// header only has inline: what the ctypes binding links
// (tools/moy_spine_binding.py). A ctypes caller passes the address of
// moy_spine_host_mem to every *_new.

#include <stdlib.h>

#include "moy_htab.h"

static void *host_alloc(size_t n) {
    return calloc(1, n ? n : 1);
}

static void host_release(void *p, size_t n) {
    (void)n;
    free(p);
}

const moy_htab_mem_t moy_spine_host_mem = { host_alloc, host_release };

// A table with no row bytes: the binding keeps each row's object itself.
moy_htab_t *moy_host_table_new(uint8_t kind, uint32_t slots) {
    return moy_htab_new(&moy_spine_host_mem, kind, slots, 0u);
}

int moy_host_table_add(moy_htab_t *t, uint32_t *h) {
    void *row;
    return moy_htab_add(t, h, &row);
}

uint32_t moy_host_table_slot_of(const moy_htab_t *t, uint32_t h) {
    return moy_htab_slot_of(t, h);
}

uint32_t moy_host_table_count(const moy_htab_t *t) {
    return moy_htab_count(t);
}

uint32_t moy_host_table_slots(const moy_htab_t *t) {
    return moy_htab_slots(t);
}

uint32_t moy_host_table_at(const moy_htab_t *t, uint32_t slot) {
    return moy_htab_at(t, slot);
}

uint32_t moy_host_table_handle(const moy_htab_t *t, uint32_t slot) {
    return moy_htab_handle(t, slot);
}

// The items handed to processes, as the store names them (moy_items.h has
// the contract).

#if defined(__has_include) && !__has_include("esp_heap_caps.h") && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L     // clock_gettime under -std=c99 (moy_sync.h)
#endif

#include "moy_items.h"

#include <string.h>

#include "moy_sync.h"

typedef struct {
    uint32_t pid;                   // 0: free
    int32_t h;
    bool rw;
    char kind[MOY_ITEMS_KIND + 1u];
    char name[MOY_ITEMS_NAME + 1u];
} slot_t;

static MOY_SYNC_LOCK_T s_lock = MOY_SYNC_LOCK_INIT;
static slot_t s_item[MOY_ITEMS];

static bool fits(const char *s, size_t n, size_t cap) {
    return s != NULL && n != 0u && n <= cap && memchr(s, 0, n) == NULL;
}

bool moy_items_hand(uint32_t pid, int32_t h, const char *kind, size_t kn, const char *name,
                    size_t nn, bool rw) {
    if (pid == 0u || h <= 0 || !fits(kind, kn, MOY_ITEMS_KIND)
        || !fits(name, nn, MOY_ITEMS_NAME)) {
        return false;
    }
    bool took = false;
    MOY_SYNC_LOCK(&s_lock);
    for (uint32_t i = 0; i < MOY_ITEMS && !took; i++) {
        slot_t *s = &s_item[i];
        if (s->pid == 0u || (s->pid == pid && s->h == h)) {
            s->pid = pid;
            s->h = h;
            memcpy(s->kind, kind, kn);
            s->kind[kn] = 0;
            memcpy(s->name, name, nn);
            s->name[nn] = 0;
            s->rw = rw;
            took = true;
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return took;
}

bool moy_items_get(uint32_t pid, int32_t h, char *kind, size_t kcap, char *name, size_t ncap,
                   bool *rw) {
    bool got = false;
    MOY_SYNC_LOCK(&s_lock);
    for (uint32_t i = 0; pid != 0u && i < MOY_ITEMS; i++) {
        const slot_t *s = &s_item[i];
        if (s->pid == pid && s->h == h) {
            size_t kn = strlen(s->kind), nn = strlen(s->name);
            if (kn < kcap && nn < ncap) {
                memcpy(kind, s->kind, kn + 1u);
                memcpy(name, s->name, nn + 1u);
                *rw = s->rw;
                got = true;
            }
            break;
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return got;
}

void moy_items_end(uint32_t pid, int32_t h) {
    MOY_SYNC_LOCK(&s_lock);
    for (uint32_t i = 0; pid != 0u && i < MOY_ITEMS; i++) {
        if (s_item[i].pid == pid && (h == 0 || s_item[i].h == h)) {
            s_item[i].pid = 0u;
        }
    }
    MOY_SYNC_UNLOCK(&s_lock);
}

void moy_items_reset(void) {
    MOY_SYNC_LOCK(&s_lock);
    memset(s_item, 0, sizeof s_item);
    MOY_SYNC_UNLOCK(&s_lock);
}

// The runtime map (moy_rt.h has the contract).

#include <string.h>

#include "moy_rt.h"

static const moy_rt_ops_t *s_rows[MOY_RT_ROWS];

const moy_rt_ops_t *moy_rt_get_n(const char *name, unsigned n) {
    for (int i = 0; i < MOY_RT_ROWS; i++) {
        const moy_rt_ops_t *r = s_rows[i];
        if (r != NULL && strlen(r->name) == n && memcmp(r->name, name, n) == 0) {
            return r;
        }
    }
    return NULL;
}

const moy_rt_ops_t *moy_rt_get(const char *name) {
    return moy_rt_get_n(name, (unsigned)strlen(name));
}

int moy_rt_add(const moy_rt_ops_t *ops) {
    int free_at = -1;
    for (int i = 0; i < MOY_RT_ROWS; i++) {
        if (s_rows[i] != NULL && strcmp(s_rows[i]->name, ops->name) == 0) {
            s_rows[i] = ops;
            return 0;
        }
        if (s_rows[i] == NULL && free_at < 0) {
            free_at = i;
        }
    }
    if (free_at < 0) {
        return -1;
    }
    s_rows[free_at] = ops;
    return 0;
}

void moy_rt_remove(const char *name) {
    for (int i = 0; i < MOY_RT_ROWS; i++) {
        if (s_rows[i] != NULL && strcmp(s_rows[i]->name, name) == 0) {
            s_rows[i] = NULL;
        }
    }
}

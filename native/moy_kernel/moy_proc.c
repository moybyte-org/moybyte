// The process table (moy_proc.h has the contract).

#include "moy_proc.h"

#include <stdlib.h>
#include <string.h>

#include "moy_htab.h"

#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define PROC_BOARD 1
#endif
#endif

// PSRAM on a board: the table keeps out of internal RAM.
static void *mem_alloc(size_t n) {
    #ifdef PROC_BOARD
    void *p = heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM);
    return p != NULL ? p : calloc(1, n);
    #else
    return calloc(1, n);
    #endif
}

static void mem_release(void *p, size_t n) {
    (void)n;
    free(p);
}

static const moy_htab_mem_t s_mem = { mem_alloc, mem_release };

// The table and the row the next runtime open takes, together in PSRAM: the
// pointer alone is internal.
typedef struct {
    moy_htab_t *tab;
    uint32_t next;
} procs_t;

static procs_t *s_p;
#define s_tab (s_p != NULL ? s_p->tab : NULL)

int moy_proc_add(uint32_t pid, uint8_t tier, uint8_t kind, const char *id,
                 uint32_t owner, uint32_t run, uint32_t *h) {
    if (s_p == NULL) {
        procs_t *p = mem_alloc(sizeof(*p));
        if (p == NULL) {
            return MOY_PROC_NOMEM;
        }
        p->tab = moy_htab_new(&s_mem, MOY_KIND_PROC, MOY_PROC_ROWS, sizeof(moy_proc_row_t));
        if (p->tab == NULL) {
            free(p);
            return MOY_PROC_NOMEM;
        }
        s_p = p;
    }
    void *row;
    int rc = moy_htab_add(s_tab, h, &row);
    if (rc != MOY_HTAB_OK) {
        return rc == MOY_HTAB_FULL ? MOY_PROC_FULL : MOY_PROC_NOMEM;
    }
    moy_proc_row_t *r = row;
    r->pid = pid;
    r->tier = tier;
    r->kind = kind;
    r->state = MOY_PROC_RUNNING;
    r->owner = owner;
    r->run = run;
    if (id != NULL) {
        size_t n = strlen(id);
        if (n >= sizeof(r->id)) {
            n = sizeof(r->id) - 1u;
        }
        memcpy(r->id, id, n);
        r->id[n] = 0;
    }
    return MOY_PROC_OK;
}

int moy_proc_get(uint32_t h, moy_proc_row_t **row) {
    void *p;
    if (s_tab == NULL || h == 0u || moy_htab_get(s_tab, h, &p) != MOY_HTAB_OK) {
        return MOY_PROC_STALE;
    }
    *row = p;
    return MOY_PROC_OK;
}

int moy_proc_end(uint32_t h, int32_t code) {
    moy_proc_row_t *r;
    if (moy_proc_get(h, &r) != MOY_PROC_OK) {
        return MOY_PROC_STALE;
    }
    r->exit = code;
    r->state = MOY_PROC_EXITED;
    if (s_p->next == h) {
        s_p->next = 0;                  // ended before any runtime opened it
    }
    moy_htab_release(s_tab, h);         // its copy of the grant goes with it
    return MOY_PROC_OK;
}

void moy_proc_next(uint32_t h) {
    if (s_p != NULL) {
        s_p->next = h;
    }
}

uint32_t moy_proc_take(void) {
    if (s_p == NULL) {
        return 0u;
    }
    uint32_t h = s_p->next;
    s_p->next = 0;
    return h;
}

int moy_proc_state(uint32_t h, uint8_t state) {
    moy_proc_row_t *r;
    if (moy_proc_get(h, &r) != MOY_PROC_OK) {
        return MOY_PROC_STALE;
    }
    r->state = state;
    return MOY_PROC_OK;
}

int moy_proc_bind(uint32_t h, void *engine, const moy_app_run_t *app) {
    moy_proc_row_t *r;
    if (moy_proc_get(h, &r) != MOY_PROC_OK) {
        return MOY_PROC_STALE;
    }
    r->engine = engine;
    if (app != NULL) {
        r->app = *app;
    } else {
        memset(&r->app, 0, sizeof(r->app));
    }
    return MOY_PROC_OK;
}

uint32_t moy_proc_slots(void) {
    return s_tab != NULL ? moy_htab_slots(s_tab) : 0u;
}

uint32_t moy_proc_at(uint32_t slot) {
    return s_tab != NULL && slot < moy_htab_slots(s_tab) && moy_htab_live(s_tab, slot)
           ? moy_htab_handle(s_tab, slot) : 0u;
}

uint32_t moy_proc_count(void) {
    return s_tab != NULL ? moy_htab_count(s_tab) : 0u;
}

uint32_t moy_proc_of_pid(uint32_t pid) {
    for (uint32_t s = 0; pid != 0u && s < moy_proc_slots(); s++) {
        if (moy_htab_live(s_tab, s) && ((moy_proc_row_t *)moy_htab_row(s_tab, s))->pid == pid) {
            return moy_htab_handle(s_tab, s);
        }
    }
    return 0u;
}

uint32_t moy_proc_of_engine(const void *engine) {
    for (uint32_t s = 0; engine != NULL && s < moy_proc_slots(); s++) {
        if (moy_htab_live(s_tab, s)
                && ((moy_proc_row_t *)moy_htab_row(s_tab, s))->engine == engine) {
            return moy_htab_handle(s_tab, s);
        }
    }
    return 0u;
}

const moy_app_run_t *moy_proc_app_of(const void *engine) {
    moy_proc_row_t *r;
    uint32_t h = moy_proc_of_engine(engine);
    if (h == 0u || moy_proc_get(h, &r) != MOY_PROC_OK || r->app.g == 0u) {
        return NULL;
    }
    return &r->app;
}

void moy_proc_reset(void) {
    if (s_p != NULL) {
        moy_htab_free(s_p->tab);
        mem_release(s_p, sizeof(*s_p));
        s_p = NULL;
    }
}

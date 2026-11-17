// The browser console's page, kept in the add-on cart: moy_page.h says what.

#include "moy_page.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "moy_net.h"
#include "moy_vol.h"
#if __has_include("moy_web_blob.h")
#include "moy_web_blob.h"
#else
extern const unsigned char moy_web_named;
extern const unsigned char moy_web_want[32];
#endif
#pragma weak moy_web_named
#pragma weak moy_web_want

// The page's state, off the internal heap (moy_net_alloc) on its first use:
// the boards' internal SRAM is the idle desk's to keep.
typedef struct {
    char root[MOY_WEB_PATH_MAX];        // the carts root the add-on is looked for in
    char path[MOY_WEB_PATH_MAX + 64];   // the pack that fits this image, under it
    uint8_t name[32];                   // a test's digest (moy_page_name)
    char say[160];                      // ABSENT's sentence (moy_page_say), "" for the default
    uint8_t offer;                      // the root links MOY_PAGE_ASK
    moy_web_pack_head_t head;           // the pack's head, while READY
} page_t;

static page_t *s_p;
static int s_read;                  // the head below is the file's
static int s_state = MOY_PAGE_NONE;
static int s_named = -1;            // -1: the image's own, else the tests'
static unsigned s_asked;            // the root's MOY_PAGE_ASK, counted

static page_t *page(void) {
    if (s_p == NULL) {
        s_p = moy_net_alloc(sizeof(*s_p));
    }
    return s_p;
}

void moy_page_name(const uint8_t *digest) {
    s_named = digest != NULL && page() != NULL;
    if (s_named) {
        memcpy(s_p->name, digest, 32);
    }
    s_read = 0;
}

int moy_page_want(uint8_t out[32]) {
    if (s_named >= 0) {
        if (s_named) {
            memcpy(out, s_p->name, 32);
        }
        return s_named;
    }
    if (&moy_web_named == NULL || !moy_web_named) {   // want rides in the same file
        return 0;
    }
    memcpy(out, moy_web_want, 32);
    return 1;
}

void moy_page_forget(void) {
    s_read = 0;
}

void moy_page_at(const char *root) {
    if (page() != NULL) {
        snprintf(s_p->root, sizeof(s_p->root), "%s", root != NULL ? root : "");
    }
    s_read = 0;
}

void moy_page_say(const char *why, int offer) {
    if (page() != NULL) {
        snprintf(s_p->say, sizeof(s_p->say), "%s", why != NULL ? why : "");
        s_p->offer = offer != 0;
    }
}

int moy_page_offers(void) {
    return s_p != NULL && s_p->offer;
}

void moy_page_ask(void) {
    s_asked++;
}

unsigned moy_page_asked(void) {
    return s_asked;
}

const char *moy_page_path(void) {
    return s_p != NULL ? s_p->path : "";
}

// The store op's bracket: the store's lock, and the panel's flush drained
// where the card shares its bus.
static int vol_of(const char *path, moy_vol_t *v, const char **rest, int *gate) {
    *gate = moy_vol_gate_enter(path);
    if (*gate < 0) {
        return -EAGAIN;
    }
    int rc = moy_vol_at(path, v, rest);
    if (rc != 0) {
        moy_vol_gate_leave(*gate);
        return -ENOENT;
    }
    return 0;
}

static int head_ok(const moy_web_pack_head_t *h, uint32_t size, const uint8_t want[32]) {
    if (memcmp(h->magic, MOY_WEB_PACK_MAGIC, 8) != 0 || memcmp(h->digest, want, 32) != 0
        || h->count == 0 || h->count > MOY_WEB_PACK_MAX
        || h->bytes <= MOY_WEB_PACK_HEAD || h->bytes != size) {
        return 0;
    }
    for (uint32_t i = 0; i < h->count; i++) {
        const moy_web_pack_entry_t *e = &h->entry[i];
        if (memchr(e->name, '\0', MOY_WEB_PACK_NAME) == NULL
            || e->off < MOY_WEB_PACK_HEAD || e->off > h->bytes
            || e->len > h->bytes - e->off) {
            return 0;
        }
    }
    return 1;
}

// A folder of the add-on's, whatever page it fits.
static int any_cart(void *ctx, const char *name, size_t len, int is_dir, uint32_t size) {
    (void)size;
    size_t n = sizeof(MOY_PAGE_CART) - 1;
    if (is_dir && len > n && memcmp(name, MOY_PAGE_CART, n) == 0) {
        *(int *)ctx = 1;
        return 1;
    }
    return 0;
}

static int some_cart(void) {
    moy_vol_t v;
    const char *rest;
    int gate, found = 0;
    if (vol_of(s_p->root, &v, &rest, &gate) == 0) {
        moy_vol_list(&v, rest, any_cart, &found);
        moy_vol_gate_leave(gate);
    }
    return found;
}

static void read_head(void) {
    s_read = 1;
    uint8_t want[32];
    if (!moy_page_want(want)) {
        s_state = MOY_PAGE_NONE;
        return;
    }
    s_state = MOY_PAGE_ABSENT;
    if (s_p == NULL || s_p->root[0] == '\0') {
        return;
    }
    static const char hex[] = "0123456789abcdef";
    char tag[13];
    for (int i = 0; i < 6; i++) {
        tag[2 * i] = hex[want[i] >> 4];
        tag[2 * i + 1] = hex[want[i] & 15];
    }
    tag[12] = '\0';
    snprintf(s_p->path, sizeof(s_p->path), "%s/" MOY_PAGE_CART "%s.moy/" MOY_PAGE_FILE,
             s_p->root, tag);
    moy_web_pack_head_t *head = &s_p->head;
    moy_vol_t v;
    const char *rest;
    int gate;
    if (vol_of(s_p->path, &v, &rest, &gate) != 0) {
        s_read = 0;
        return;
    }
    moy_vol_file_t *f = NULL;
    size_t got = 0;
    uint32_t size = 0;
    if (moy_vol_open(&v, rest, MOY_VOL_READ, &f) == 0) {
        s_state = MOY_PAGE_STALE;
        if (moy_vol_size(f, &size) != 0
            || moy_vol_read(f, head, sizeof(*head), &got) != 0) {
            got = 0;
        }
        moy_vol_close(f);
    }
    moy_vol_gate_leave(gate);
    if (got == sizeof(*head) && head_ok(head, size, want)) {
        s_state = MOY_PAGE_READY;
    } else if (s_state == MOY_PAGE_ABSENT && some_cart()) {
        s_state = MOY_PAGE_STALE;
    }
}

int moy_page_state(void) {
    if (!s_read) {
        read_head();
    }
    return s_state;
}

const char *moy_page_why(void) {
    switch (moy_page_state()) {
    case MOY_PAGE_READY:
        return NULL;
    case MOY_PAGE_STALE:
        return MOY_PAGE_STALE_WHY;
    case MOY_PAGE_ABSENT:
        return s_p != NULL && s_p->say[0] ? s_p->say : MOY_PAGE_ABSENT_WHY;
    default:
        return "this firmware names no browser console";
    }
}

int moy_page_find(const char *name, uint32_t *off, uint32_t *len) {
    if (moy_page_state() != MOY_PAGE_READY) {
        return -ENOENT;
    }
    for (uint32_t i = 0; i < s_p->head.count; i++) {
        if (strcmp(s_p->head.entry[i].name, name) == 0) {
            *off = s_p->head.entry[i].off;
            *len = s_p->head.entry[i].len;
            return 0;
        }
    }
    return -ENOENT;
}

const char *moy_page_asset(unsigned i, uint32_t *len) {
    if (moy_page_state() != MOY_PAGE_READY || i >= s_p->head.count) {
        return NULL;
    }
    *len = s_p->head.entry[i].len;
    return s_p->head.entry[i].name;
}

// moy_str: the string table and its language pack (moy_str.h).

#include <stdio.h>
#include <string.h>

#include "moy_str.h"

#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define STR_ALLOC(n) heap_caps_malloc((n), MALLOC_CAP_SPIRAM)
#define STR_FREE(p) heap_caps_free(p)
#else
#include <stdlib.h>
#define STR_ALLOC(n) malloc(n)
#define STR_FREE(p) free(p)
#endif

// The pack's entries, NULL where English reads; the array itself is
// allocated at the first entry, so an image that installs none holds nothing.
static char **s_pack;

const char *moy_str(int id) {
    if (id < 0 || id >= MOY_STRINGS) {
        return "";
    }
    if (s_pack != NULL && s_pack[id] != NULL) {
        return s_pack[id];
    }
    return moy_str_en[id];
}

int moy_str_id(const char *name, size_t n) {
    for (int i = 0; i < MOY_STRINGS; i++) {
        if (strlen(moy_str_ids[i]) == n && memcmp(moy_str_ids[i], name, n) == 0) {
            return i;
        }
    }
    return -1;
}

// The next conversion at or after s[*i] ("%%" skipped): 's', 'u', 'd', 'x'
// (only as %08x), '?' for any other, or 0 at the end.
static char next_conv(const char *s, size_t n, size_t *i) {
    while (*i < n) {
        if (s[*i] != '%') {
            (*i)++;
            continue;
        }
        if (*i + 1 < n && s[*i + 1] == '%') {
            *i += 2;
            continue;
        }
        size_t j = *i + 1;
        *i = j + 1;
        if (j < n && (s[j] == 's' || s[j] == 'u' || s[j] == 'd')) {
            return s[j];
        }
        if (j + 2 < n && memcmp(s + j, "08x", 3) == 0) {
            *i = j + 3;
            return 'x';
        }
        return '?';
    }
    return 0;
}

static int same_convs(const char *a, size_t an, const char *b, size_t bn) {
    size_t i = 0, j = 0;
    for (;;) {
        char ca = next_conv(a, an, &i), cb = next_conv(b, bn, &j);
        if (ca != cb || ca == '?') {
            return 0;
        }
        if (ca == 0) {
            return 1;
        }
    }
}

int moy_str_set(int id, const char *text, size_t n) {
    if (id < 0 || id >= MOY_STRINGS) {
        return MOY_STR_BAD;
    }
    if (text == NULL) {
        if (s_pack != NULL && s_pack[id] != NULL) {
            STR_FREE(s_pack[id]);
            s_pack[id] = NULL;
        }
        return MOY_STR_OK;
    }
    if (memchr(text, 0, n) != NULL ||
        !same_convs(moy_str_en[id], strlen(moy_str_en[id]), text, n)) {
        moy_str_set(id, NULL, 0);
        return MOY_STR_REFUSED;
    }
    if (s_pack == NULL) {
        s_pack = STR_ALLOC(sizeof(char *) * MOY_STRINGS);
        if (s_pack == NULL) {
            return MOY_STR_NOMEM;
        }
        memset(s_pack, 0, sizeof(char *) * MOY_STRINGS);
    }
    char *p = STR_ALLOC(n + 1);
    if (p == NULL) {
        return MOY_STR_NOMEM;
    }
    memcpy(p, text, n);
    p[n] = 0;
    if (s_pack[id] != NULL) {
        STR_FREE(s_pack[id]);
    }
    s_pack[id] = p;
    return MOY_STR_OK;
}

void moy_str_reset(void) {
    for (int i = 0; s_pack != NULL && i < MOY_STRINGS; i++) {
        moy_str_set(i, NULL, 0);
    }
}

int moy_str_fmt(char *out, size_t n, int id, ...) {
    va_list ap;
    va_start(ap, id);
    int r = vsnprintf(out, n, moy_str(id), ap);
    va_end(ap);
    return r;
}

int moy_str_copy(char *out, size_t n, int id) {
    const char *s = moy_str(id);
    size_t len = strlen(s);
    if (n == 0) {
        return (int)len;
    }
    size_t k = len < n - 1 ? len : n - 1;
    while (k > 0 && k < len && ((unsigned char)s[k] & 0xC0) == 0x80) {
        k--;                                    // never mid-codepoint
    }
    memcpy(out, s, k);
    out[k] = 0;
    return (int)len;
}

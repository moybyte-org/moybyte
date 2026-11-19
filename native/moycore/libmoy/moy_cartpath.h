/* The rules a cart's file names are held to before a host sees one, which
 * both bindings share: a name inside the cart's folder (SPEC.md 16.6) and a
 * path the manifest's "writable" declares (SPEC.md 9.1, 16.12). Internal to
 * libmoy, beside moy_lua.c and moy_wasm.c. */

#ifndef MOY_CARTPATH_H_INCLUDED
#define MOY_CARTPATH_H_INCLUDED

#include <string.h>

#include "moy.h"

/* Relative, '/'-separated, no empty, "." or ".." segment, no '\\'. */
static inline int moy_path_inside(const char *name)
{
    const char *seg = name, *p;
    if (!*name) return 0;
    for (;;) {
        size_t len;
        p = strchr(seg, '/');
        len = p ? (size_t)(p - seg) : strlen(seg);
        if (len == 0) return 0;
        if (len == 1 && seg[0] == '.') return 0;
        if (len == 2 && seg[0] == '.' && seg[1] == '.') return 0;
        if (memchr(seg, '\\', len)) return 0;
        if (!p) return 1;
        seg = p + 1;
    }
}

/* Is `path` one the "writable" entries declare? They are NUL-terminated, one
 * after another, ended by an empty one, or NULL for none. An entry ending in
 * '/' is a folder, and declares every path below it; any other entry
 * declares itself. An entry that breaks the path rule declares nothing. */
static inline int moy_path_writable(const char *entries, const char *path)
{
    const char *e = entries;
    if (!e) return 0;
    for (; *e; e += strlen(e) + 1) {
        size_t n = strlen(e);
        char folder[MOY_FILE_PATH_MAX + 1];
        if (n > MOY_FILE_PATH_MAX) continue;
        if (e[n - 1] != '/') {
            if (moy_path_inside(e) && !strcmp(e, path)) return 1;
            continue;
        }
        memcpy(folder, e, n - 1);
        folder[n - 1] = 0;
        if (moy_path_inside(folder) && !strncmp(e, path, n) && path[n]) return 1;
    }
    return 0;
}

/* The path `write` or `erase` may name, as a C string in `buf`: NULL unless
 * it is well-formed, no longer than MOY_FILE_PATH_MAX, and declared. */
static inline const char *moy_path_written(const char *entries, const uint8_t *s,
                                           size_t len, char buf[MOY_FILE_PATH_MAX + 1])
{
    if (len > MOY_FILE_PATH_MAX || memchr(s, 0, len)) return NULL;
    memcpy(buf, s, len);
    buf[len] = 0;
    return moy_path_inside(buf) && moy_path_writable(entries, buf) ? buf : NULL;
}

#endif

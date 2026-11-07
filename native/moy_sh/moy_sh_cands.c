// moy_sh_cands: what Tab completes to (moy_sh.h), one rule for every face:
// the Terminal's (moy_sh_face.c) and the stream face's (native/moy_tty).

#include <stdlib.h>
#include <string.h>

#include "moy_sh.h"

#define SEPS " ;|&()<>"

static int is_sep(char c) {
    return c != '\0' && strchr(SEPS, c) != NULL;
}

static int add_name(void *arg, const char *name, int is_dir, uint32_t size) {
    (void)size;
    moy_sh_cands_t *a = arg;
    size_t pn = strlen(a->prefix);
    if (a->listing && strncmp(name, a->prefix, pn) != 0) {
        return 0;
    }
    size_t sn = a->stem_n, nn = strlen(name);
    size_t need = sn + nn + (is_dir ? 1 : 0) + 1;
    if (a->n >= MOY_SH_CANDS_MAX || a->used + need > a->cap) {
        return 0;
    }
    char *p = a->buf + a->used;
    memcpy(p, a->stem, sn);
    memcpy(p + sn, name, nn);
    if (is_dir) {
        p[sn + nn] = '/';
    }
    p[need - 1] = '\0';
    a->names[a->n++] = p;
    a->used += need;
    return 0;
}

static int by_name(const void *x, const void *y) {
    return strcmp(*(const char *const *)x, *(const char *const *)y);
}

int moy_sh_cands(moy_sh_t *s, const char *line, size_t col, moy_sh_cands_t *a) {
    size_t start = col;
    while (start > 0 && !is_sep(line[start - 1])) {
        start--;
    }
    a->n = 0;
    a->used = 0;
    a->start = start;
    a->word_n = col - start;
    a->stem_n = 0;
    a->stem[0] = '\0';
    if (a->word_n >= sizeof a->word) {
        return 0;
    }
    memcpy(a->word, line + start, a->word_n);
    a->word[a->word_n] = '\0';
    size_t b = start;
    while (b > 0 && line[b - 1] == ' ') {
        b--;
    }
    if (strchr(a->word, '/') == NULL && (b == 0 || is_sep(line[b - 1]))) {
        // a command's name
        a->listing = 0;
        a->prefix = a->word;
        moy_sh_complete(s, a->word, add_name, a);
        qsort(a->names, (size_t)a->n, sizeof a->names[0], by_name);
        int k = 0;
        for (int i = 0; i < a->n; i++) {
            if (k == 0 || strcmp(a->names[k - 1], a->names[i]) != 0) {
                a->names[k++] = a->names[i];
            }
        }
        a->n = k;
        return a->n;
    }
    // a path: its folder's names (an argument with no slash names the
    // working folder), each carrying the folder as typed
    const char *slash = strrchr(a->word, '/');
    size_t sl = slash != NULL ? (size_t)(slash - a->word) + 1 : 0;
    memcpy(a->stem, a->word, sl);
    a->stem[sl] = '\0';
    a->stem_n = sl;
    char folder[sizeof a->word] = ".";
    if (sl > 0) {
        memcpy(folder, a->stem, sl + 1);
    }
    while (sl > 1 && folder[sl - 1] == '/') {
        folder[--sl] = '\0';
    }
    a->listing = 1;
    a->prefix = a->word + a->stem_n;
    moy_sh_list(s, folder, add_name, a);
    qsort(a->names, (size_t)a->n, sizeof a->names[0], by_name);
    return a->n;
}

size_t moy_sh_cands_common(const moy_sh_cands_t *a) {
    if (a->n == 0) {
        return 0;
    }
    size_t common = strlen(a->names[0]);
    for (int i = 1; i < a->n; i++) {
        size_t k = 0;
        while (k < common && a->names[i][k] == a->names[0][k]) {
            k++;
        }
        common = k;
    }
    return common;
}

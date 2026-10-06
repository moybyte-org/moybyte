// The journal (moy_journal.h has the contract; runtime/moy_journal.py is the
// reference every branch below follows).

#include <stdio.h>
#include <string.h>

#include "moy_arena.h"
#include "moy_journal.h"
#include "moy_json.h"

#define JDIR "journal"
#define LOG "journal.jsonl"
#define CURSOR "cursor.json"
#define SNAPS "s"

typedef struct {
    int64_t seq;
    const char *file;
    size_t file_n;
    const char *snap;
    int has_len, has_crc, has_grad, stamped;
    int64_t len, crc, grad;
    const char *line;           // the line's object, scanned
    const char *line_e;
} jent_t;

typedef struct {
    const char *file;
    size_t file_n;
    int64_t seq;
} cur_t;

typedef struct {
    moy_arena_t a;
    const char *cart;
    char *jdir, *log, *cur, *sdir;
    jent_t *e;
    size_t n, cap;
    cur_t *c;
    size_t nc, capc;
} jr_t;

// -- small things -----------------------------------------------------------------

static const char *i64(int64_t v, char *buf) {
    char *p = buf + 23;
    uint64_t u = v < 0 ? (uint64_t)0 - (uint64_t)v : (uint64_t)v;
    *p = 0;
    do {
        *--p = (char)('0' + u % 10u);
        u /= 10u;
    } while (u);
    if (v < 0) {
        *--p = '-';
    }
    return p;
}

static int same_name(const char *a, size_t an, const char *b, size_t bn) {
    return an == bn && memcmp(a, b, an) == 0;
}

static int in_files(const char *f, size_t fn, const char *const *files,
                    size_t nfiles) {
    if (files == NULL) {
        return 1;
    }
    for (size_t i = 0; i < nfiles; i++) {
        if (same_name(f, fn, files[i], strlen(files[i]))) {
            return 1;
        }
    }
    return 0;
}

static int jr_open(jr_t *j, const char *cart) {
    memset(j, 0, sizeof *j);
    j->cart = cart;
    j->jdir = moy_arena_join(&j->a, cart, JDIR);
    if (j->jdir == NULL) {
        return MOY_ENOMEM;
    }
    j->log = moy_arena_join(&j->a, j->jdir, LOG);
    j->cur = moy_arena_join(&j->a, j->jdir, CURSOR);
    j->sdir = moy_arena_join(&j->a, j->jdir, SNAPS);
    return j->a.failed ? MOY_ENOMEM : 0;
}

// -- reading -----------------------------------------------------------------------

static int add_entry(jr_t *j, const jent_t *e) {
    if (j->n == j->cap) {
        size_t cap = j->cap ? j->cap * 2u : 32u;
        jent_t *ne = moy_arena_alloc(&j->a, cap * sizeof(jent_t));
        if (ne == NULL) {
            return MOY_ENOMEM;
        }
        if (j->n) {
            memcpy(ne, j->e, j->n * sizeof(jent_t));
        }
        j->e = ne;
        j->cap = cap;
    }
    // ascending by seq, a tie after its equals (Python's sort is stable)
    size_t k = j->n;
    while (k > 0 && j->e[k - 1u].seq > e->seq) {
        j->e[k] = j->e[k - 1u];
        k--;
    }
    j->e[k] = *e;
    j->n++;
    return 0;
}

// `_journal_load_entries`: every line that reads as an entry, by seq.
static int load_entries(jr_t *j) {
    moy_buf_t b;
    if (moy_fs_read_file(j->log, (size_t)-1, &b) != 0) {
        return 0;
    }
    char *t = moy_arena_dup(&j->a, b.p, b.n);
    size_t n = b.n;
    moy_buf_free(&b);
    if (t == NULL) {
        return MOY_ENOMEM;
    }
    const char *p = t, *end = t + n;
    while (p < end) {
        const char *nl = memchr(p, '\n', (size_t)(end - p));
        const char *le = nl != NULL ? nl : end;
        const char *s = moy_json_ws(p, le);
        const char *e = s < le ? moy_json_value(s, le, 1u) : NULL;
        p = nl != NULL ? nl + 1 : end;
        if (e == NULL || moy_json_ws(e, le) != le || *s != '{') {
            continue;                   // torn or corrupt: dropped, the rest kept
        }
        jent_t ent;
        memset(&ent, 0, sizeof ent);
        const char *x, *xe;
        if (!moy_json_get(s, e, "seq", &x, &xe) || moy_json_kind(x, xe) != MOY_JSON_INT
            || moy_json_int(x, xe, &ent.seq) != 1) {
            continue;
        }
        if (!moy_json_get(s, e, "file", &x, &xe) || *x != '"') {
            continue;
        }
        char *f = moy_arena_alloc(&j->a, moy_json_strlen(x, xe) + 1u);
        if (f == NULL) {
            return MOY_ENOMEM;
        }
        ent.file_n = moy_json_str(x, xe, f);
        f[ent.file_n] = 0;
        ent.file = f;
        if (!moy_json_get(s, e, "snap", &x, &xe) || *x != '"') {
            continue;
        }
        char *sn = moy_arena_alloc(&j->a, moy_json_strlen(x, xe) + 1u);
        if (sn == NULL) {
            return MOY_ENOMEM;
        }
        sn[moy_json_str(x, xe, sn)] = 0;
        ent.snap = sn;
        ent.has_len = moy_json_get(s, e, "len", &x, &xe) && *x != 'n';
        if (ent.has_len && moy_json_int(x, xe, &ent.len) != 1) {
            ent.len = -1;               // a len int() cannot read matches nothing
        }
        ent.has_crc = moy_json_get(s, e, "crc", &x, &xe) && *x != 'n';
        if (ent.has_crc && moy_json_int(x, xe, &ent.crc) != 1) {
            ent.crc = -1;
        }
        ent.stamped = moy_json_get(s, e, "stamped", &x, &xe) && moy_json_truthy(x, xe);
        ent.has_grad = moy_json_get(s, e, "grad", &x, &xe);
        if (ent.has_grad && moy_json_int(x, xe, &ent.grad) != 1) {
            ent.has_grad = 0;
        }
        ent.line = s;
        ent.line_e = e;
        int rc = add_entry(j, &ent);
        if (rc != 0) {
            return rc;
        }
    }
    return 0;
}

static cur_t *cursor_of(jr_t *j, const char *f, size_t fn) {
    for (size_t i = 0; i < j->nc; i++) {
        if (same_name(j->c[i].file, j->c[i].file_n, f, fn)) {
            return &j->c[i];
        }
    }
    return NULL;
}

static int cursor_put(jr_t *j, const char *f, size_t fn, int64_t seq) {
    cur_t *c = cursor_of(j, f, fn);
    if (c != NULL) {
        c->seq = seq;
        return 0;
    }
    if (j->nc == j->capc) {
        size_t cap = j->capc ? j->capc * 2u : 8u;
        cur_t *nc = moy_arena_alloc(&j->a, cap * sizeof(cur_t));
        if (nc == NULL) {
            return MOY_ENOMEM;
        }
        if (j->nc) {
            memcpy(nc, j->c, j->nc * sizeof(cur_t));
        }
        j->c = nc;
        j->capc = cap;
    }
    j->c[j->nc++] = (cur_t){ f, fn, seq };
    return 0;
}

// The newest seq of each file, in the order the files first appear.
static int newest(jr_t *j) {
    j->nc = 0;
    for (size_t i = 0; i < j->n; i++) {
        int rc = cursor_put(j, j->e[i].file, j->e[i].file_n, j->e[i].seq);
        if (rc != 0) {
            return rc;
        }
    }
    return 0;
}

// `_journal_cursors`: the newest of each file, where cursor.json's map says
// otherwise for a file the journal has.
static int load_cursors(jr_t *j) {
    int rc = newest(j);
    if (rc != 0) {
        return rc;
    }
    moy_buf_t b;
    if (moy_fs_read(j->cur, NULL, &b) != 0) {
        return 0;
    }
    char *t = moy_arena_dup(&j->a, b.p, b.n);
    size_t n = b.n;
    moy_buf_free(&b);
    if (t == NULL) {
        return MOY_ENOMEM;
    }
    const char *s = moy_json_ws(t, t + n);
    const char *e = moy_json_value(s, t + n, 1u);
    const char *m, *me;
    if (e == NULL || moy_json_ws(e, t + n) != t + n || *s != '{'
        || !moy_json_get(s, e, "cursors", &m, &me) || *m != '{') {
        return 0;
    }
    moy_json_iter_t it;
    const char *k, *ke, *v, *ve;
    moy_json_iter(&it, m, me);
    while (moy_json_next(&it, &k, &ke, &v, &ve)) {
        for (size_t i = 0; i < j->nc; i++) {
            int64_t sv;
            if (moy_json_str_is(k, ke, j->c[i].file, j->c[i].file_n)
                && moy_json_int(v, ve, &sv) == 1) {
                j->c[i].seq = sv;
            }
        }
    }
    return 0;
}

static int64_t total_len(const jr_t *j) {
    int64_t t = 0;
    for (size_t i = 0; i < j->n; i++) {
        if (j->e[i].has_len && j->e[i].len > 0) {
            t += j->e[i].len;
        }
    }
    return t;
}

// -- writing -----------------------------------------------------------------------

typedef struct {
    char *p;
    size_t n, cap;
    int failed;
} sb_t;

static void sb_put(sb_t *b, const char *s, size_t n) {
    if (b->failed) {
        return;
    }
    if (b->n + n + 1u > b->cap) {
        size_t cap = b->cap ? b->cap : 256u;
        while (cap < b->n + n + 1u) {
            cap *= 2u;
        }
        char *p = moy_store_alloc(cap);
        if (p == NULL) {
            b->failed = 1;
            return;
        }
        if (b->n) {
            memcpy(p, b->p, b->n);
        }
        moy_store_free(b->p, b->cap);
        b->p = p;
        b->cap = cap;
    }
    memcpy(b->p + b->n, s, n);
    b->n += n;
    b->p[b->n] = 0;
}

static void sb_str(sb_t *b, const char *s) {
    sb_put(b, s, strlen(s));
}

static void sb_quote(sb_t *b, const char *s, size_t n) {
    size_t k = moy_json_quote(s, n, NULL, 0);
    char *t = moy_store_alloc(k + 1u);
    if (t == NULL) {
        b->failed = 1;
        return;
    }
    moy_json_quote(s, n, t, k);
    sb_put(b, t, k);
    moy_store_free(t, k + 1u);
}

static void sb_canon(sb_t *b, const char *v, const char *ve) {
    size_t k = moy_json_canon(v, ve, NULL, 0);
    char *t = k == MOY_JSON_DEEP ? NULL : moy_store_alloc(k + 1u);
    if (t == NULL) {
        b->failed = 1;
        return;
    }
    moy_json_canon(v, ve, t, k);
    sb_put(b, t, k);
    moy_store_free(t, k + 1u);
}

static void sb_free(sb_t *b) {
    moy_store_free(b->p, b->cap);
    memset(b, 0, sizeof *b);
}

// `_journal_write_cursors`: the map published, or removed when it says what
// an absent one says (every file at its newest entry).
static int write_cursors(jr_t *j) {
    int def = 1;
    int64_t top = 0;
    for (size_t i = 0; i < j->nc; i++) {
        int64_t nw = -1;
        for (size_t k = 0; k < j->n; k++) {
            if (same_name(j->e[k].file, j->e[k].file_n, j->c[i].file, j->c[i].file_n)) {
                nw = j->e[k].seq;
            }
        }
        if (nw != j->c[i].seq) {
            def = 0;
        }
        if (j->c[i].seq > top) {
            top = j->c[i].seq;
        }
    }
    for (size_t k = 0; k < j->n && def; k++) {
        if (cursor_of(j, j->e[k].file, j->e[k].file_n) == NULL) {
            def = 0;
        }
    }
    if (def) {
        moy_fs_remove(j->cur);
        moy_fs_forget_bak(j->cur);
        return 0;
    }
    sb_t b = { NULL, 0, 0, 0 };
    char num[24];
    sb_str(&b, "{\"cursors\": {");
    for (size_t i = 0; i < j->nc; i++) {
        if (i) {
            sb_str(&b, ", ");
        }
        sb_quote(&b, j->c[i].file, j->c[i].file_n);
        sb_str(&b, ": ");
        sb_str(&b, i64(j->c[i].seq, num));
    }
    sb_str(&b, "}, \"seq\": ");
    sb_str(&b, i64(top, num));
    sb_str(&b, "}");
    int rc = b.failed ? MOY_ENOMEM : moy_fs_publish(j->cur, b.p, b.n);
    sb_free(&b);
    return rc;
}

// `_journal_rewrite`: the log as the kept entries, published whole.
static int rewrite(jr_t *j) {
    sb_t b = { NULL, 0, 0, 0 };
    sb_put(&b, "", 0);
    for (size_t i = 0; i < j->n; i++) {
        sb_canon(&b, j->e[i].line, j->e[i].line_e);
        sb_str(&b, "\n");
    }
    int rc = b.failed ? MOY_ENOMEM : moy_fs_publish(j->log, b.p, b.n);
    sb_free(&b);
    return rc;
}

static void drop_dead_cursors(jr_t *j) {
    size_t k = 0;
    for (size_t i = 0; i < j->nc; i++) {
        int live = 0;
        for (size_t e = 0; e < j->n && !live; e++) {
            live = same_name(j->e[e].file, j->e[e].file_n, j->c[i].file, j->c[i].file_n);
        }
        if (live) {
            j->c[k++] = j->c[i];
        }
    }
    j->nc = k;
}

static void remove_snap(jr_t *j, const jent_t *e) {
    char *p = moy_arena_join(&j->a, j->jdir, e->snap);
    if (p != NULL) {
        moy_fs_remove(p);
    }
}

// `_journal_read_snap`: the snapshot, checked against its recorded length (and
// its stamp when it is a claimed backup), or MOY_FS_NONE.
static int read_snap(jr_t *j, const jent_t *e, moy_buf_t *out) {
    char *p = moy_arena_join(&j->a, j->jdir, e->snap);
    if (p == NULL) {
        return MOY_ENOMEM;
    }
    int rc = e->stamped ? moy_fs_read_stamped(p, out)
                        : moy_fs_read_file(p, (size_t)-1, out);
    if (rc != 0) {
        return MOY_FS_NONE;
    }
    uint32_t chars, crc;
    moy_fs_stamp(out->p, out->n, &chars, &crc);
    if ((e->has_len && (int64_t)chars != e->len) || (!e->has_len && !e->stamped && out->n == 0)) {
        moy_buf_free(out);
        return MOY_FS_NONE;
    }
    return 0;
}

int moy_journal_graduate(const char *cart, int value) {
    moy_arena_t a;
    memset(&a, 0, sizeof a);
    char *p = moy_arena_join(&a, cart, "manifest.json");
    moy_buf_t b;
    int done = 0;
    if (p != NULL && moy_fs_read(p, NULL, &b) == 0) {
        const char *s = moy_json_ws(b.p, b.p + b.n);
        const char *e = moy_json_value(s, b.p + b.n, 1u);
        if (e != NULL && moy_json_ws(e, b.p + b.n) == b.p + b.n && *s == '{') {
            const char *g, *ge;
            int now = moy_json_get(s, e, "graduated", &g, &ge) && moy_json_truthy(g, ge);
            if (now != (value != 0)) {
                const char *val = value ? "true" : NULL;
                size_t n = moy_json_canon_set(s, e, "graduated", val, NULL, 0);
                char *t = n == MOY_JSON_DEEP ? NULL : moy_arena_alloc(&a, n + 1u);
                if (t != NULL) {
                    moy_json_canon_set(s, e, "graduated", val, t, n);
                    done = moy_fs_publish(p, t, n) == 0;
                }
            }
        }
        moy_buf_free(&b);
    }
    moy_arena_free(&a);
    return done;
}

static void apply_grad(jr_t *j, const jent_t *e) {
    if (e->has_grad) {
        moy_journal_graduate(j->cart, e->grad != 0);
    }
}

// -- the walks ---------------------------------------------------------------------

// `_journal_undo_target`: the file whose undo the bar takes, the entry to
// restore and the file's cursor after.
static int undo_target(jr_t *j, const char *const *files, size_t nfiles,
                       const jent_t **target, cur_t **which) {
    int64_t best = -1;
    *target = NULL;
    for (size_t i = 0; i < j->nc; i++) {
        cur_t *c = &j->c[i];
        if (!in_files(c->file, c->file_n, files, nfiles)) {
            continue;
        }
        const jent_t *prev = NULL;
        int found = 0, first = 1;
        for (size_t k = 0; k < j->n; k++) {
            const jent_t *e = &j->e[k];
            if (!same_name(e->file, e->file_n, c->file, c->file_n)) {
                continue;
            }
            if (e->seq == c->seq) {
                found = !first;
                break;
            }
            prev = e;
            first = 0;
        }
        if (found && c->seq > best) {
            best = c->seq;
            *target = prev;
            *which = c;
        }
    }
    return *target != NULL;
}

// `_journal_redo_target`: the nearest next commit among the files.
static int redo_target(jr_t *j, const char *const *files, size_t nfiles,
                       const jent_t **target, cur_t **which) {
    *target = NULL;
    for (size_t i = 0; i < j->nc; i++) {
        cur_t *c = &j->c[i];
        if (!in_files(c->file, c->file_n, files, nfiles)) {
            continue;
        }
        for (size_t k = 0; k < j->n; k++) {
            const jent_t *e = &j->e[k];
            if (same_name(e->file, e->file_n, c->file, c->file_n) && e->seq > c->seq) {
                if (*target == NULL || e->seq < (*target)->seq) {
                    *target = e;
                    *which = c;
                }
                break;
            }
        }
    }
    return *target != NULL;
}

static int walk(const char *cart, int redo, const char *const *files,
                size_t nfiles, char *out, size_t cap) {
    jr_t j;
    int rc = jr_open(&j, cart);
    if (rc == 0) {
        rc = load_entries(&j);
    }
    if (rc == 0 && j.n) {
        rc = load_cursors(&j);
    }
    const jent_t *t;
    cur_t *c;
    if (rc != 0 || j.n == 0
        || !(redo ? redo_target(&j, files, nfiles, &t, &c)
                  : undo_target(&j, files, nfiles, &t, &c))) {
        moy_arena_free(&j.a);
        return rc ? -rc : 0;
    }
    moy_buf_t b;
    if (read_snap(&j, t, &b) != 0) {
        moy_arena_free(&j.a);
        return 0;                       // missing or torn: refused, the live file intact
    }
    char *live = moy_arena_join(&j.a, cart, t->file);
    rc = live == NULL ? MOY_ENOMEM : moy_fs_publish(live, b.p, b.n);
    moy_buf_free(&b);
    if (rc == 0) {
        c->seq = t->seq;
        rc = write_cursors(&j);
    }
    if (rc == 0) {
        apply_grad(&j, t);
        if (cap) {
            size_t n = t->file_n < cap - 1u ? t->file_n : cap - 1u;
            memcpy(out, t->file, n);
            out[n] = 0;
        }
    }
    moy_arena_free(&j.a);
    return rc ? -rc : 1;
}

int moy_journal_undo(const char *cart, const char *const *files, size_t nfiles,
                     char *out, size_t cap) {
    return walk(cart, 0, files, nfiles, out, cap);
}

int moy_journal_redo(const char *cart, const char *const *files, size_t nfiles,
                     char *out, size_t cap) {
    return walk(cart, 1, files, nfiles, out, cap);
}

int moy_journal_can(const char *cart, int redo, const char *const *files,
                    size_t nfiles) {
    jr_t j;
    int ok = 0;
    if (jr_open(&j, cart) == 0 && load_entries(&j) == 0 && j.n
        && load_cursors(&j) == 0) {
        const jent_t *t;
        cur_t *c;
        ok = redo ? redo_target(&j, files, nfiles, &t, &c)
                  : undo_target(&j, files, nfiles, &t, &c);
    }
    moy_arena_free(&j.a);
    return ok;
}

// -- compaction ----------------------------------------------------------------------

static int compact(jr_t *j) {
    int64_t total = total_len(j);
    size_t dropped = 0;
    for (size_t k = 0; k < j->n && (j->n - dropped > MOY_JOURNAL_MAX_ENTRIES
                                    || total > (int64_t)MOY_JOURNAL_MAX_BYTES); k++) {
        jent_t *e = &j->e[k];
        cur_t *c = cursor_of(j, e->file, e->file_n);
        if (c == NULL || e->seq >= c->seq) {
            continue;                   // a file's current state, or its redo tail
        }
        if (e->has_len && e->len > 0) {
            total -= e->len;
        }
        e->line = NULL;                 // marked
        dropped++;
    }
    if (dropped == 0) {
        return 0;
    }
    size_t keep = 0;
    for (size_t k = 0; k < j->n; k++) {
        if (j->e[k].line == NULL) {
            remove_snap(j, &j->e[k]);
        } else {
            j->e[keep++] = j->e[k];
        }
    }
    j->n = keep;
    int rc = rewrite(j);
    if (rc == 0) {
        drop_dead_cursors(j);
        rc = write_cursors(j);
    }
    return rc ? -rc : (int)dropped;
}

int moy_journal_compact(const char *cart) {
    jr_t j;
    int rc = jr_open(&j, cart);
    if (rc == 0) {
        rc = load_entries(&j);
    }
    if (rc == 0 && j.n) {
        rc = load_cursors(&j);
        rc = rc == 0 ? compact(&j) : -rc;
    } else {
        rc = rc ? -rc : 0;
    }
    moy_arena_free(&j.a);
    return rc;
}

// -- the commit ----------------------------------------------------------------------

// `_journal_put_snap`: the live file's spent publish backup claimed when its
// stamp is these bytes', else the bytes written.
static int put_snap(const char *live, const char *dest, const char *data,
                    size_t n, uint32_t chars, uint32_t crc, int *stamped) {
    *stamped = 0;
    if (moy_fs_claim(live, dest, chars, crc)) {
        *stamped = 1;
        return 0;
    }
    return moy_fs_write(dest, data, n);
}

static int append(jr_t *j, const char *file, const char *data, size_t n, int grad,
                  const char *ops, size_t ops_n, int64_t ts, uint32_t *seq_out) {
    size_t fn = strlen(file);
    *seq_out = 0;
    int rc = load_entries(j);
    if (rc == 0) {
        rc = load_cursors(j);
    }
    if (rc != 0) {
        return rc;
    }
    uint32_t chars, crc;
    moy_fs_stamp(data, n, &chars, &crc);
    cur_t *cf = cursor_of(j, file, fn);
    const jent_t *now = NULL;
    if (cf != NULL) {
        for (size_t k = 0; k < j->n; k++) {
            if (same_name(j->e[k].file, j->e[k].file_n, file, fn) && j->e[k].seq <= cf->seq) {
                now = &j->e[k];
            }
        }
    }
    if (now != NULL) {
        int current;
        if (now->has_crc) {
            current = now->has_len && now->len == (int64_t)chars && now->crc == (int64_t)crc;
        } else {
            moy_buf_t b;
            char *p = moy_arena_join(&j->a, j->jdir, now->snap);
            current = 0;
            if (p != NULL && moy_fs_read_file(p, (size_t)-1, &b) == 0) {
                current = b.n == n && memcmp(b.p, data, n) == 0;
                moy_buf_free(&b);
            }
        }
        if (current) {
            return 0;                   // nothing changed: nothing written
        }
    }
    // A commit of a rewound file cuts its redo tail, and only its.
    size_t cut = 0;
    for (size_t k = 0; k < j->n; k++) {
        jent_t *e = &j->e[k];
        if (same_name(e->file, e->file_n, file, fn) && (cf == NULL || e->seq > cf->seq)) {
            remove_snap(j, e);
            e->line = NULL;
            cut++;
        }
    }
    if (cut) {
        size_t keep = 0;
        for (size_t k = 0; k < j->n; k++) {
            if (j->e[k].line != NULL) {
                j->e[keep++] = j->e[k];
            }
        }
        j->n = keep;
        rc = rewrite(j);
        if (rc != 0) {
            return rc;
        }
        drop_dead_cursors(j);
    }
    int64_t seq = j->n ? j->e[j->n - 1u].seq + 1 : 1;
    size_t sn = fn + 32u;
    char *snap = moy_arena_alloc(&j->a, sn);
    if (snap == NULL) {
        return MOY_ENOMEM;
    }
    char num[24];
    const char *digits = i64(seq, num);
    size_t dn = strlen(digits), k = 0;
    memcpy(snap, SNAPS "/", 2);
    k = 2;
    for (size_t pad = dn; pad < 4; pad++) {
        snap[k++] = '0';
    }
    memcpy(snap + k, digits, dn);
    k += dn;
    snap[k++] = '-';
    for (size_t i = 0; i < fn; i++) {
        snap[k++] = file[i] == '/' ? '_' : file[i];
    }
    snap[k] = 0;
    char *dest = moy_arena_join(&j->a, j->jdir, snap);
    char *live = moy_arena_join(&j->a, j->cart, file);
    if (dest == NULL || live == NULL) {
        return MOY_ENOMEM;
    }
    int stamped;
    rc = put_snap(live, dest, data, n, chars, crc, &stamped);
    if (rc != 0) {                      // the folders are made by failing to write in them
        moy_fs_mkdir(j->jdir);
        moy_fs_mkdir(j->sdir);
        rc = put_snap(live, dest, data, n, chars, crc, &stamped);
        if (rc != 0) {
            return rc;
        }
    }
    sb_t b = { NULL, 0, 0, 0 };
    sb_str(&b, "{\"seq\": ");
    sb_str(&b, i64(seq, num));
    sb_str(&b, ", \"ts\": ");
    sb_str(&b, i64(ts, num));
    sb_str(&b, ", \"file\": ");
    sb_quote(&b, file, fn);
    sb_str(&b, ", \"snap\": ");
    sb_quote(&b, snap, strlen(snap));
    sb_str(&b, ", \"len\": ");
    sb_str(&b, i64(chars, num));
    sb_str(&b, ", \"crc\": ");
    sb_str(&b, i64(crc, num));
    if (stamped) {
        sb_str(&b, ", \"stamped\": 1");
    }
    if (grad >= 0) {
        sb_str(&b, ", \"grad\": ");
        sb_str(&b, i64(grad, num));
    }
    if (ops != NULL && ops_n) {
        const char *s = moy_json_ws(ops, ops + ops_n);
        const char *e = moy_json_value(s, ops + ops_n, 1u);
        if (e != NULL && moy_json_truthy(s, e)) {
            sb_str(&b, ", \"ops\": ");
            sb_canon(&b, s, e);
        }
    }
    sb_str(&b, "}\n");
    if (b.failed) {
        sb_free(&b);
        return MOY_ENOMEM;
    }
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f;
    rc = moy_vol_at(j->log, &v, &rest);
    if (rc == 0) {
        rc = moy_vol_open(&v, rest, MOY_VOL_APPEND, &f);
    }
    if (rc == 0) {
        rc = moy_vol_write(f, b.p, b.n);
        int rc2 = moy_vol_close(f);
        if (rc == 0) {
            rc = rc2;
        }
    }
    // the new entry, as the rest of the call reads it
    char *line = rc == 0 ? moy_arena_dup(&j->a, b.p, b.n - 1u) : NULL;
    sb_free(&b);
    if (rc != 0) {
        return rc;
    }
    if (line == NULL) {
        return MOY_ENOMEM;
    }
    jent_t ne;
    memset(&ne, 0, sizeof ne);
    ne.seq = seq;
    ne.file = moy_arena_dup(&j->a, file, fn);
    ne.file_n = fn;
    ne.snap = snap;
    ne.has_len = ne.has_crc = 1;
    ne.len = chars;
    ne.crc = crc;
    ne.stamped = stamped;
    ne.has_grad = grad >= 0;
    ne.grad = grad;
    ne.line = line;
    ne.line_e = line + strlen(line);
    if (ne.file == NULL || add_entry(j, &ne) != 0) {
        return MOY_ENOMEM;
    }
    rc = cursor_put(j, ne.file, fn, seq);
    if (rc == 0) {
        rc = write_cursors(j);          // the cursor last
    }
    if (rc != 0) {
        return rc;
    }
    if (grad >= 0) {
        moy_journal_graduate(j->cart, grad != 0);
    }
    *seq_out = (uint32_t)seq;
    if (j->n > MOY_JOURNAL_MAX_ENTRIES || total_len(j) > (int64_t)MOY_JOURNAL_MAX_BYTES) {
        rc = compact(j);
        return rc < 0 ? -rc : 0;
    }
    return 0;
}

int moy_journal_append(const char *cart, const char *file, const char *data,
                       size_t n, int grad, const char *ops, size_t ops_n,
                       int64_t ts, uint32_t *seq) {
    jr_t j;
    int rc = jr_open(&j, cart);
    if (rc == 0) {
        rc = append(&j, file, data, n, grad, ops, ops_n, ts, seq);
    }
    moy_arena_free(&j.a);
    return rc;
}

// -- #136: the timeline's reads ----------------------------------------------------------

int moy_journal_list(const char *cart, const char *file, moy_jent_fn fn,
                     void *ctx) {
    jr_t j;
    int rc = jr_open(&j, cart);
    if (rc == 0) {
        rc = load_entries(&j);
    }
    for (size_t k = 0; rc == 0 && k < j.n; k++) {
        const jent_t *e = &j.e[k];
        if (file != NULL && !same_name(e->file, e->file_n, file, strlen(file))) {
            continue;
        }
        rc = fn(ctx, (uint32_t)e->seq, e->line, (size_t)(e->line_e - e->line));
    }
    moy_arena_free(&j.a);
    return rc;
}

static const jent_t *by_seq(const jr_t *j, uint32_t seq) {
    for (size_t k = 0; k < j->n; k++) {
        if (j->e[k].seq == (int64_t)seq) {
            return &j->e[k];
        }
    }
    return NULL;
}

int moy_journal_snap(const char *cart, uint32_t seq, moy_buf_t *out) {
    jr_t j;
    out->p = NULL;
    out->n = 0;
    int rc = jr_open(&j, cart);
    if (rc == 0) {
        rc = load_entries(&j);
    }
    const jent_t *e = rc == 0 ? by_seq(&j, seq) : NULL;
    if (rc == 0) {
        rc = e != NULL ? read_snap(&j, e, out) : MOY_FS_NONE;
    }
    moy_arena_free(&j.a);
    return rc;
}

int moy_journal_restore(const char *cart, uint32_t seq, int64_t ts,
                        uint32_t *seq_out) {
    jr_t j;
    *seq_out = 0;
    int rc = jr_open(&j, cart);
    if (rc == 0) {
        rc = load_entries(&j);
    }
    const jent_t *e = rc == 0 ? by_seq(&j, seq) : NULL;
    moy_buf_t b = { NULL, 0 };
    if (rc == 0) {
        rc = e != NULL ? read_snap(&j, e, &b) : MOY_FS_NONE;
    }
    char *file = rc == 0 ? moy_arena_dup(&j.a, e->file, e->file_n) : NULL;
    char *live = file != NULL ? moy_arena_join(&j.a, cart, file) : NULL;
    if (rc == 0 && live == NULL) {
        rc = MOY_ENOMEM;
    }
    if (rc == 0) {
        rc = moy_fs_publish(live, b.p, b.n);
    }
    jr_t k;
    if (rc == 0) {
        rc = jr_open(&k, cart);
        if (rc == 0) {
            rc = append(&k, file, b.p, b.n, -1, NULL, 0, ts, seq_out);
        }
        moy_arena_free(&k.a);
    }
    moy_buf_free(&b);
    moy_arena_free(&j.a);
    return rc;
}

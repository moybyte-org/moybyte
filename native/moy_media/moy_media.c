// moy_media: the media service (moy_media.h has the contract).

#include "moy_media.h"

#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "minimp3.h"
#include "moy_net.h"
#include "moy_ota.h"
#include "moy_play.h"

#if defined(__has_include)
// ESP_PLATFORM is not defined for a usermod's sources on the esp32 port, so a
// board is recognised by the header it has.
#if __has_include("esp_heap_caps.h")
#define MEDIA_BOARD 1
#endif
#endif

#ifdef MEDIA_BOARD
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "moy_aud.h"
#endif

#define ONE 65536u
#define IN_MASK (MOY_MEDIA_IN_BYTES - 1u)
#define OUT_MASK (MOY_MEDIA_PCM_FRAMES - 1u)
#define NO_TITLE 0xffffu
#define RD_BYTES 4096u
#define META_MAX (255u * 16u)

_Static_assert((MOY_MEDIA_IN_BYTES & IN_MASK) == 0, "the stream buffer is a power of two");
_Static_assert((MOY_MEDIA_PCM_FRAMES & OUT_MASK) == 0, "the decoded ring is a power of two");

typedef struct {
    uint32_t owner;
    int out_rate, out_ch;
    uint32_t out_need;              // the decoded ring's room one frame may take
    uint32_t share;                 // what the open took from the MEDIA row

    // the list and the commands: under the lock
    uint16_t e_src[MOY_MEDIA_ENTRIES], e_title[MOY_MEDIA_ENTRIES];
    int n;
    uint32_t pool_n;
    char pool[MOY_MEDIA_LIST_BYTES];
    uint32_t gen;                   // bumped by every command that moves the entry
    int entry;                      // the entry `gen` names
    int soft;                       // `gen` is the task's own advance: the tail plays out
    int stop;
    volatile int idle;              // stopped and let go
    uint32_t armed;                 // the generation a feed is taken for, 0 for none
    int fed_end;
    char title[MOY_MEDIA_TEXT_MAX], station[MOY_MEDIA_TEXT_MAX];
    uint32_t text_gen;
    int list_played;                // an entry of this list decoded a frame

    // the task's
    uint32_t seen;                  // the generation it works on
    int cur;                        // the entry it works on
    int kind;
    volatile int state;
    int err;
    int played;                     // the entry decoded a frame
    int retries;
    uint32_t dial_ms;               // since when an opening URL waits for the link, 0 none
    int eof;                        // the entry's source has no more bytes
    int h;                          // the URL's client, 0 for none
    uint32_t clen;                  // its Content-Length: 0 for a stream with no end
    uint32_t icy_int, icy_left;
    int in_meta;
    uint32_t meta_need, meta_got;
    uint32_t skip;                  // bytes to drop (an ID3v2 tag)
    int head_seen;
    volatile int rate, channels, kbps;
    // the entry's place: frames decoded at the output's rate, the MP3 bytes
    // they took, and a fed file's size (the face's, under the lock)
    volatile uint32_t e_dec, e_used;
    uint32_t e_size;

    // the stream buffer: one writer (the task, or the VM's feed under the lock), one reader
    volatile uint32_t in_w, in_r;
    uint8_t in[MOY_MEDIA_IN_BYTES];

    // the decoder
    mp3dec_t dec;
    uint32_t win_off, win_n;
    uint8_t win[MOY_MEDIA_WIN_BYTES];
    int16_t pcm[MINIMP3_MAX_SAMPLES_PER_FRAME];
    int rs_hz;
    uint32_t rs_inc, rs_step, rs_pos;
    int32_t rs_acc[2], rs_cnt, rs_prev[2];

    // the decoded ring: one writer (the task), one reader (the pull)
    volatile uint32_t out_w, out_r;
    volatile uint32_t flush_gen, flush_at;
    uint32_t flush_seen;
    int16_t *out;                   // MOY_MEDIA_PCM_FRAMES x out_ch, after the state

    // the pull's
    volatile int volume, paused, muted;
    int32_t gain, ramp;
    int started;
    volatile uint32_t decoded, pulled, starved;

    uint8_t rd[RD_BYTES];
    char meta[META_MAX + 1];
    char url[MOY_MEDIA_SRC_MAX];

#ifdef MEDIA_BOARD
    StaticSemaphore_t mtx_buf;
    SemaphoreHandle_t mtx;
    TaskHandle_t task;
    StaticTask_t *tcb;                  // internal, as a TCB must be
    StackType_t *stack;                 // PSRAM, MOY_MEDIA_STACK
#endif
} media_t;

static media_t *s_m;

#ifdef MEDIA_BOARD
#define LOCK(m) xSemaphoreTake((m)->mtx, portMAX_DELAY)
#define UNLOCK(m) xSemaphoreGive((m)->mtx)
#define WAKE(m) xTaskNotifyGive((m)->task)
#else
#define LOCK(m) ((void)(m))
#define UNLOCK(m) ((void)(m))
#define WAKE(m) ((void)(m))
#endif

#define LOAD(p) __atomic_load_n((p), __ATOMIC_ACQUIRE)
#define STORE(p, v) __atomic_store_n((p), (v), __ATOMIC_RELEASE)

static void *block_alloc(size_t n) {
#ifdef MEDIA_BOARD
    return heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#else
    return calloc(1, n);
#endif
}

static void block_free(void *p) {
#ifdef MEDIA_BOARD
    heap_caps_free(p);
#else
    free(p);
#endif
}

static uint32_t share_of(int ch) {
    return (uint32_t)sizeof(media_t) + MOY_MEDIA_PCM_FRAMES * (uint32_t)ch * sizeof(int16_t)
           + MOY_MEDIA_STACK + MOY_MEDIA_CONN;
}

uint32_t moy_media_share(void) {
#ifdef MEDIA_BOARD
    return share_of(moy_aud_out_channels());
#else
    return share_of(1);
#endif
}

int moy_media_present(void) {
#ifdef MEDIA_BOARD
    int st = moy_aud_out_state(NULL);
    return st != MOY_AUD_OUT_FAILED && st != MOY_AUD_OUT_ABSENT;
#else
    return 1;
#endif
}

uint32_t moy_media_owner(void) {
    return s_m != NULL ? s_m->owner : 0u;
}

static media_t *mine(uint32_t owner) {
    return s_m != NULL && owner != 0u && s_m->owner == owner ? s_m : NULL;
}

// -- text ------------------------------------------------------------------------

static int utf8_ok(const unsigned char *p, size_t n) {
    size_t i = 0;
    while (i < n) {
        unsigned c = p[i];
        size_t k;
        if (c < 0x80) {
            k = 0;
        } else if (c >= 0xc2 && c <= 0xdf) {
            k = 1;
        } else if (c >= 0xe0 && c <= 0xef) {
            k = 2;
        } else if (c >= 0xf0 && c <= 0xf4) {
            k = 3;
        } else {
            return 0;
        }
        if (i + k >= n && k > 0) {
            return 0;
        }
        for (size_t j = 1; j <= k; j++) {
            if ((p[i + j] & 0xc0) != 0x80) {
                return 0;
            }
        }
        i += k + 1;
    }
    return 1;
}

// `s` (UTF-8, else read as Latin-1) into `out`, cut on a character: its length.
static size_t to_utf8(const char *s, size_t n, char *out, size_t cap) {
    const unsigned char *p = (const unsigned char *)s;
    size_t o = 0;
    if (cap == 0) {
        return 0;
    }
    if (utf8_ok(p, n)) {
        size_t k = n < cap - 1 ? n : cap - 1;
        while (k > 0 && k < n && (p[k] & 0xc0) == 0x80) {
            k--;
        }
        memcpy(out, s, k);
        o = k;
    } else {
        for (size_t i = 0; i < n; i++) {
            if (p[i] < 0x80) {
                if (o + 1 >= cap) {
                    break;
                }
                out[o++] = (char)p[i];
            } else {
                if (o + 2 >= cap) {
                    break;
                }
                out[o++] = (char)(0xc0 | (p[i] >> 6));
                out[o++] = (char)(0x80 | (p[i] & 0x3f));
            }
        }
    }
    out[o] = '\0';
    return o;
}

int moy_media_icy_title(const char *meta, size_t n, char *out, size_t cap) {
    static const char key[] = "StreamTitle='";
    const size_t kn = sizeof(key) - 1;
    while (n > 0 && meta[n - 1] == '\0') {
        n--;
    }
    for (size_t i = 0; i + kn <= n; i++) {
        if (memcmp(meta + i, key, kn) != 0) {
            continue;
        }
        const char *v = meta + i + kn, *end = meta + n, *e = NULL;
        for (const char *q = v; q + 1 < end; q++) {
            if (q[0] == '\'' && q[1] == ';') {
                e = q;
                break;
            }
        }
        if (e == NULL) {
            e = end;
            while (e > v && e[-1] != '\'') {
                e--;
            }
            e = e > v ? e - 1 : end;
        }
        return (int)to_utf8(v, (size_t)(e - v), out, cap);
    }
    return -1;
}

static int lower(int c) {
    return c >= 'A' && c <= 'Z' ? c + 32 : c;
}

static int has_ci(const char *s, const char *pat) {
    size_t pn = strlen(pat);
    for (; s != NULL && *s; s++) {
        size_t j = 0;
        while (j < pn && s[j] && lower((unsigned char)s[j]) == pat[j]) {
            j++;
        }
        if (j == pn) {
            return 1;
        }
    }
    return 0;
}

static int starts_ci(const char *s, size_t n, const char *pat) {
    size_t pn = strlen(pat);
    if (n < pn) {
        return 0;
    }
    for (size_t i = 0; i < pn; i++) {
        if (lower((unsigned char)s[i]) != pat[i]) {
            return 0;
        }
    }
    return 1;
}

int moy_media_list_kind(const char *ctype, const char *path, const char *text, size_t n) {
    if (ctype != NULL) {
        if (has_ci(ctype, "mpegurl")) {
            return 1;
        }
        if (has_ci(ctype, "scpls") || has_ci(ctype, "x-pls")) {
            return 2;
        }
    }
    if (path != NULL) {
        size_t e = 0;
        while (path[e] && path[e] != '?' && path[e] != '#') {
            e++;
        }
        if ((e >= 4 && starts_ci(path + e - 4, 4, ".m3u")) || (e >= 5 && starts_ci(path + e - 5, 5, ".m3u8"))) {
            return 1;
        }
        if (e >= 4 && starts_ci(path + e - 4, 4, ".pls")) {
            return 2;
        }
    }
    if (text != NULL) {
        size_t i = 0;
        if (n >= 3 && (unsigned char)text[0] == 0xef && (unsigned char)text[1] == 0xbb
            && (unsigned char)text[2] == 0xbf) {
            i = 3;
        }
        while (i < n && (text[i] == ' ' || text[i] == '\t' || text[i] == '\r' || text[i] == '\n')) {
            i++;
        }
        if (starts_ci(text + i, n - i, "[playlist]")) {
            return 2;
        }
        if (starts_ci(text + i, n - i, "#extm3u")) {
            return 1;
        }
    }
    return 0;
}

// -- the list (the caller holds the lock) ------------------------------------------

static int put(media_t *m, const char *s, size_t n) {
    if (m->pool_n + n + 1 > MOY_MEDIA_LIST_BYTES) {
        return -1;
    }
    int at = (int)m->pool_n;
    memcpy(m->pool + at, s, n);
    m->pool[at + (int)n] = '\0';
    m->pool_n += (uint32_t)n + 1u;
    return at;
}

static int add_locked(media_t *m, const char *src, size_t sn, const char *title, size_t tn) {
    if (m->n >= MOY_MEDIA_ENTRIES || sn == 0 || sn >= MOY_MEDIA_SRC_MAX) {
        return -MOY_MEDIA_ENOSPC;
    }
    uint32_t was = m->pool_n;
    int s = put(m, src, sn);
    int t = title != NULL && tn > 0 ? put(m, title, tn < MOY_MEDIA_TEXT_MAX ? tn : MOY_MEDIA_TEXT_MAX - 1) : -2;
    if (s < 0 || t == -1) {
        m->pool_n = was;
        return -MOY_MEDIA_ENOSPC;
    }
    m->e_src[m->n] = (uint16_t)s;
    m->e_title[m->n] = t < 0 ? NO_TITLE : (uint16_t)t;
    return m->n++;
}

static int has_scheme(const char *s, size_t n) {
    for (size_t i = 0; i + 2 < n && s[i] != '/'; i++) {
        if (s[i] == ':' && s[i + 1] == '/' && s[i + 2] == '/') {
            return 1;
        }
    }
    return 0;
}

static int is_url(const char *s) {
    return has_scheme(s, strlen(s));
}

// `ref` resolved against `base` (a URL, or a file's path) into `out`: a URL
// as it is; "/x" as it is on a path, on the base's host on a URL; else beside
// the base. Its length, 0 when it does not fit.
static size_t resolve(const char *base, const char *ref, size_t rn, char *out, size_t cap) {
    size_t bn = 0;
    int slash = 0;
    if (base != NULL && !has_scheme(ref, rn)) {
        const char *sch = strstr(base, "://");
        const char *host_end = NULL;
        if (sch != NULL) {
            host_end = strchr(sch + 3, '/');
            if (host_end == NULL) {
                host_end = base + strlen(base);
            }
        }
        if (rn > 0 && ref[0] == '/') {
            bn = host_end != NULL ? (size_t)(host_end - base) : 0;
        } else {
            const char *q = strchr(base, '?');
            bn = q != NULL ? (size_t)(q - base) : strlen(base);
            while (bn > 0 && base[bn - 1] != '/') {
                bn--;
            }
            if (host_end != NULL && base + bn <= host_end) {
                bn = (size_t)(host_end - base);
                slash = 1;
            }
        }
    }
    if (bn + (size_t)slash + rn + 1 > cap) {
        return 0;
    }
    memcpy(out, base, bn);
    if (slash) {
        out[bn++] = '/';
    }
    memcpy(out + bn, ref, rn);
    out[bn + rn] = '\0';
    return bn + rn;
}

static const char *trim(const char *a, const char **b) {
    while (a < *b && (*a == ' ' || *a == '\t')) {
        a++;
    }
    while (*b > a && ((*b)[-1] == ' ' || (*b)[-1] == '\t' || (*b)[-1] == '\r')) {
        (*b)--;
    }
    return a;
}

static int parse_locked(media_t *m, const char *t, size_t n, const char *base) {
    int kind = moy_media_list_kind(NULL, NULL, t, n);
    const char *f_at[MOY_MEDIA_ENTRIES + 1], *t_at[MOY_MEDIA_ENTRIES + 1];
    size_t f_n[MOY_MEDIA_ENTRIES + 1], t_n[MOY_MEDIA_ENTRIES + 1];
    char buf[MOY_MEDIA_SRC_MAX];
    const char *title = NULL;
    size_t title_n = 0;
    int added = 0;
    memset(f_at, 0, sizeof(f_at));
    memset(t_at, 0, sizeof(t_at));
    const char *p = t, *end = t + n;
    while (p < end) {
        const char *e = memchr(p, '\n', (size_t)(end - p));
        if (e == NULL) {
            e = end;
        }
        const char *le = e;
        const char *s = trim(p, &le);
        size_t ln = (size_t)(le - s);
        p = e + 1;
        if (ln == 0) {
            continue;
        }
        if (kind == 2) {
            int file = starts_ci(s, ln, "file"), tit = starts_ci(s, ln, "title");
            if (!file && !tit) {
                continue;
            }
            const char *d = s + (file ? 4 : 5);
            int idx = 0;
            while (d < le && *d >= '0' && *d <= '9') {
                idx = idx * 10 + (*d++ - '0');
                if (idx > MOY_MEDIA_ENTRIES) {
                    break;
                }
            }
            if (d >= le || *d != '=' || idx < 1 || idx > MOY_MEDIA_ENTRIES) {
                continue;
            }
            d++;
            const char *ve = le;
            d = trim(d, &ve);
            if (file) {
                f_at[idx] = d;
                f_n[idx] = (size_t)(ve - d);
            } else {
                t_at[idx] = d;
                t_n[idx] = (size_t)(ve - d);
            }
            continue;
        }
        if (starts_ci(s, ln, "#extinf:")) {
            const char *c = memchr(s, ',', ln);
            title = c != NULL ? c + 1 : NULL;
            title_n = c != NULL ? (size_t)(le - title) : 0;
            continue;
        }
        if (s[0] == '#') {
            continue;
        }
        size_t k = resolve(base, s, ln, buf, sizeof(buf));
        if (k > 0 && add_locked(m, buf, k, title, title_n) >= 0) {
            added++;
        }
        title = NULL;
        title_n = 0;
    }
    for (int i = 1; kind == 2 && i <= MOY_MEDIA_ENTRIES; i++) {
        if (f_at[i] == NULL) {
            continue;
        }
        size_t k = resolve(base, f_at[i], f_n[i], buf, sizeof(buf));
        if (k > 0 && add_locked(m, buf, k, t_at[i], t_at[i] != NULL ? t_n[i] : 0) >= 0) {
            added++;
        }
    }
    return added;
}

// -- the commands ------------------------------------------------------------------

int moy_media_clear(uint32_t owner) {
    media_t *m = mine(owner);
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    LOCK(m);
    m->n = 0;
    m->pool_n = 0;
    m->entry = -1;
    m->gen++;
    m->soft = 0;
    m->list_played = 0;
    UNLOCK(m);
    WAKE(m);
    return 0;
}

int moy_media_add(uint32_t owner, const char *src, const char *title) {
    media_t *m = mine(owner);
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    LOCK(m);
    int rc = add_locked(m, src, strlen(src), title, title != NULL ? strlen(title) : 0);
    UNLOCK(m);
    return rc;
}

int moy_media_playlist(uint32_t owner, const char *text, size_t n, const char *base) {
    media_t *m = mine(owner);
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    LOCK(m);
    int rc = parse_locked(m, text, n, base);
    UNLOCK(m);
    return rc;
}

int moy_media_go(uint32_t owner, int i) {
    media_t *m = mine(owner);
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    LOCK(m);
    if (i < 0 || i >= m->n) {
        UNLOCK(m);
        return -MOY_MEDIA_EINVAL;
    }
    m->entry = i;
    m->gen++;
    m->soft = 0;
    m->stop = 0;
    m->idle = 0;
    m->muted = 0;
    m->err = 0;
    UNLOCK(m);
    WAKE(m);
    return 0;
}

int moy_media_stop(uint32_t owner) {
    media_t *m = mine(owner);
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    m->muted = 1;
    LOCK(m);
    m->stop = 1;
    m->armed = 0;
    UNLOCK(m);
    WAKE(m);
    return 0;
}

int moy_media_pause(uint32_t owner, int on) {
    media_t *m = mine(owner);
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    m->paused = on ? 1 : 0;
    WAKE(m);
    return 0;
}

int moy_media_volume(uint32_t owner, int v) {
    media_t *m = mine(owner);
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    m->volume = v < 0 ? 0 : v > 100 ? 100 : v;
    return 0;
}

static uint32_t in_room(const media_t *m) {
    return MOY_MEDIA_IN_BYTES - (LOAD(&m->in_w) - LOAD(&m->in_r));
}

// `n` bytes into the stream buffer at its write end (its one writer).
static void in_put(media_t *m, const uint8_t *p, uint32_t n) {
    uint32_t w = m->in_w;
    uint32_t at = w & IN_MASK, first = MOY_MEDIA_IN_BYTES - at;
    if (first > n) {
        first = n;
    }
    memcpy(m->in + at, p, first);
    memcpy(m->in, p + first, n - first);
    STORE(&m->in_w, w + n);
}

int moy_media_feed(uint32_t owner, uint32_t gen, const void *buf, size_t n) {
    media_t *m = mine(owner);
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    uint32_t k = 0;
    LOCK(m);
    if (gen != 0 && m->armed == gen && !m->fed_end) {
        k = in_room(m);
        if (k > n) {
            k = (uint32_t)n;
        }
        in_put(m, buf, k);
    }
    UNLOCK(m);
    if (k > 0) {
        WAKE(m);
    }
    return (int)k;
}

int moy_media_feed_end(uint32_t owner, uint32_t gen) {
    media_t *m = mine(owner);
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    LOCK(m);
    if (gen != 0 && m->armed == gen) {
        m->fed_end = 1;
    }
    UNLOCK(m);
    WAKE(m);
    return 0;
}

int moy_media_feed_size(uint32_t owner, uint32_t gen, uint32_t size) {
    media_t *m = mine(owner);
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    LOCK(m);
    if (gen != 0 && m->armed == gen) {
        m->e_size = size;
    }
    UNLOCK(m);
    return 0;
}

int moy_media_status(moy_media_status_t *st) {
    memset(st, 0, sizeof(*st));
    media_t *m = s_m;
    if (m == NULL) {
        return 0;
    }
    LOCK(m);
    st->state = m->state;
    st->paused = m->paused;
    st->owner = m->owner;
    st->gen = m->seen;
    st->entry = m->cur;
    st->entries = m->n;
    st->kind = m->kind;
    st->armed = m->armed != 0 && m->armed == m->seen && !m->fed_end;
    st->rate = m->rate;
    st->channels = m->channels;
    st->kbps = m->kbps;
    st->volume = m->volume;
    st->err = m->err;
    st->text_gen = m->text_gen;
    st->idle = m->idle;
    uint32_t size = m->e_size;
    UNLOCK(m);
    uint32_t dec = m->e_dec, used = m->e_used;
    uint32_t held = LOAD(&m->out_w) - LOAD(&m->out_r);
    uint32_t heard = dec > held ? dec - held : 0;
    if (m->out_rate > 0) {
        st->at_ms = (uint32_t)((uint64_t)heard * 1000u / (uint32_t)m->out_rate);
        if (size > 0 && used > 0) {
            st->len_ms = (uint32_t)((uint64_t)size * dec / used * 1000u / (uint32_t)m->out_rate);
        }
    }
    st->in_bytes = LOAD(&m->in_w) - LOAD(&m->in_r);
    st->in_room = MOY_MEDIA_IN_BYTES - st->in_bytes;
    st->pcm_frames = LOAD(&m->out_w) - LOAD(&m->out_r);
    st->decoded = m->decoded;
    st->played = m->pulled;
    st->starved = m->starved;
    return 1;
}

static int copy_text(const char *s, char *out, size_t cap) {
    size_t n = strlen(s);
    if (cap == 0) {
        return 0;
    }
    if (n >= cap) {
        n = cap - 1;
    }
    memcpy(out, s, n);
    out[n] = '\0';
    return (int)n;
}

int moy_media_text(int which, char *out, size_t cap) {
    media_t *m = s_m;
    if (cap > 0) {
        out[0] = '\0';
    }
    if (m == NULL) {
        return 0;
    }
    int n = 0;
    LOCK(m);
    int i = m->cur;
    if (which == MOY_MEDIA_T_TITLE) {
        n = copy_text(m->title, out, cap);
    } else if (which == MOY_MEDIA_T_STATION) {
        n = copy_text(m->station, out, cap);
    } else if (i >= 0 && i < m->n) {
        if (which == MOY_MEDIA_T_SRC) {
            n = copy_text(m->pool + m->e_src[i], out, cap);
        } else if (m->e_title[i] != NO_TITLE) {
            n = copy_text(m->pool + m->e_title[i], out, cap);
        }
    }
    UNLOCK(m);
    return n;
}

int moy_media_entry(int i, char *src, size_t src_cap, char *title, size_t title_cap) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EINVAL;
    }
    LOCK(m);
    if (i < 0 || i >= m->n) {
        UNLOCK(m);
        return -MOY_MEDIA_EINVAL;
    }
    int n = copy_text(m->pool + m->e_src[i], src, src_cap);
    copy_text(m->e_title[i] != NO_TITLE ? m->pool + m->e_title[i] : "", title, title_cap);
    UNLOCK(m);
    return n;
}

// -- the task ------------------------------------------------------------------------

static void conn_close(media_t *m) {
    if (m->h > 0) {
        moy_httpc_close(m->h);
    }
    m->h = 0;
}

static void set_text(media_t *m, char *dst, const char *s, size_t n) {
    char t[MOY_MEDIA_TEXT_MAX];
    to_utf8(s, n, t, sizeof(t));
    LOCK(m);
    if (strcmp(dst, t) != 0) {
        strcpy(dst, t);
        m->text_gen++;
    }
    UNLOCK(m);
}

// The entry moves on by itself: again for a URL that played and has retries
// left, else the next. A command since wins.
static void advance(media_t *m, int err) {
    m->err = err;
    conn_close(m);
    LOCK(m);
    if (m->gen == m->seen && !m->stop) {
        int again = m->kind == MOY_MEDIA_URL && m->clen == 0 && m->played
                    && m->retries < MOY_MEDIA_RETRIES;
        m->entry = again ? m->cur : m->cur + 1;
        m->gen++;
        m->soft = 1;
    }
    UNLOCK(m);
}

// Entry `e` begins: what the last one held dropped (the decoded tail too,
// unless the move was the task's own), the state OPENING for a URL.
static void begin(media_t *m) {
    conn_close(m);
    LOCK(m);
    int e = m->entry, soft = m->soft;
    int again = e == m->cur && soft;
    m->seen = m->gen;
    m->armed = 0;
    m->fed_end = 0;
    STORE(&m->in_r, LOAD(&m->in_w));
    m->cur = e;
    int kind = e >= 0 && e < m->n ? (is_url(m->pool + m->e_src[e]) ? MOY_MEDIA_URL : MOY_MEDIA_FEED) : 0;
    if (kind != 0) {
        // a new entry's place; past the list's end the last one's stays
        m->e_size = 0;
        m->e_dec = 0;
        m->e_used = 0;
    }
    if (kind == MOY_MEDIA_FEED) {
        m->armed = m->seen;
    }
    if (e < 0) {
        m->state = MOY_MEDIA_IDLE;
    } else if (kind == 0) {
        m->state = m->list_played ? MOY_MEDIA_ENDED : MOY_MEDIA_FAILED;
    } else {
        m->state = kind == MOY_MEDIA_URL ? MOY_MEDIA_OPENING : MOY_MEDIA_PLAYING;
    }
    if (!again) {
        m->title[0] = '\0';
        m->station[0] = '\0';
        m->text_gen++;
    }
    UNLOCK(m);
    m->kind = kind;
    m->retries = again ? m->retries + 1 : 0;
    m->dial_ms = 0;
    m->played = 0;
    m->eof = 0;
    m->skip = 0;
    m->head_seen = 0;
    m->icy_int = 0;
    m->in_meta = 0;
    m->win_off = 0;
    m->win_n = 0;
    m->rs_hz = 0;
    mp3dec_init(&m->dec);
    if (!soft) {
        STORE(&m->flush_at, LOAD(&m->out_w));
        STORE(&m->flush_gen, m->flush_gen + 1u);
    }
}

static int header_int(int h, const char *name) {
    char v[16];
    if (moy_httpc_header(h, name, v, sizeof(v)) <= 0) {
        return 0;
    }
    int x = 0;
    for (const char *p = v; *p >= '0' && *p <= '9'; p++) {
        x = x * 10 + (*p - '0');
        if (x > 1 << 20) {
            return 0;
        }
    }
    return x;
}

// A URL's playlist body read whole into the stream buffer's memory, parsed,
// and the list replaced by it unless a command came since.
static void take_list(media_t *m) {
    uint32_t n = 0;
    while (n < MOY_MEDIA_IN_BYTES - 1u) {
        int k = moy_httpc_read(m->h, m->in + n, MOY_MEDIA_IN_BYTES - 1u - n);
        if (k <= 0) {
            break;
        }
        n += (uint32_t)k;
    }
    conn_close(m);
    STORE(&m->in_r, LOAD(&m->in_w));
    LOCK(m);
    int got = 0;
    if (m->gen == m->seen && !m->stop) {
        m->n = 0;
        m->pool_n = 0;
        got = parse_locked(m, (const char *)m->in, n, m->url);
        m->entry = 0;
        m->gen++;
        m->soft = 0;
    }
    UNLOCK(m);
    if (got == 0) {
        m->err = MOY_MEDIA_EINVAL;
    }
}

#ifdef MEDIA_BOARD
extern int moy_net_dialing(void) __attribute__((weak));

// The station the console asked for, waited for here, a step at a time so a
// command still reaches the task: 1 while it associates (MOY_NET_DIAL_MS at
// most), 0 once the URL may open.
static int awaiting_link(media_t *m) {
    if (moy_net_dialing == NULL || !moy_net_dialing()) {
        return 0;
    }
    uint32_t ms = (uint32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS) | 1u;
    if (m->dial_ms == 0) {
        m->dial_ms = ms;
    }
    return ms - m->dial_ms < MOY_NET_DIAL_MS;
}
#endif

// 0 while the URL waits for the link (the task polls again), else 1.
static int open_url(media_t *m) {
    #ifdef MEDIA_BOARD
    if (awaiting_link(m)) {
        return 0;
    }
    #endif
    LOCK(m);
    copy_text(m->pool + m->e_src[m->cur], m->url, sizeof(m->url));
    UNLOCK(m);
    int status = 0;
    uint32_t clen = 0;
    int h = moy_httpc_open_ex(m->url, MOY_MEDIA_AGENT, MOY_HTTPC_MEDIA | MOY_HTTPC_ICY,
                              &status, &clen);
    if (h < 0) {
        advance(m, -h);
        return 1;
    }
    m->h = h;
    m->clen = clen;
    if (status != 200) {
        advance(m, 5);                  // EIO: no stream behind it
        return 1;
    }
    char ctype[64] = "";
    moy_httpc_header(h, "content-type", ctype, sizeof(ctype));
    const char *path = strstr(m->url, "://");
    path = path != NULL ? strchr(path + 3, '/') : NULL;
    if (moy_media_list_kind(ctype, path, NULL, 0) != 0) {
        take_list(m);
        return 1;
    }
    char name[MOY_MEDIA_TEXT_MAX];
    int nn = moy_httpc_header(h, "icy-name", name, sizeof(name));
    if (nn > 0) {
        set_text(m, m->station, name, (size_t)nn);
    }
    m->icy_int = (uint32_t)header_int(h, "icy-metaint");
    m->icy_left = m->icy_int;
    m->state = MOY_MEDIA_PLAYING;
    return 1;
}

// A connection's bytes, its ICY metadata taken out, into the stream buffer.
static void demux(media_t *m, const uint8_t *p, uint32_t k) {
    while (k > 0) {
        if (m->icy_int == 0) {
            in_put(m, p, k);
            return;
        }
        if (m->in_meta) {
            uint32_t t = m->meta_need - m->meta_got;
            t = t < k ? t : k;
            memcpy(m->meta + m->meta_got, p, t);
            m->meta_got += t;
            p += t;
            k -= t;
            if (m->meta_got == m->meta_need) {
                char title[MOY_MEDIA_TEXT_MAX];
                int tn = moy_media_icy_title(m->meta, m->meta_got, title, sizeof(title));
                if (tn >= 0) {
                    set_text(m, m->title, title, (size_t)tn);
                }
                m->in_meta = 0;
                m->icy_left = m->icy_int;
            }
        } else if (m->icy_left == 0) {
            m->meta_need = (uint32_t)p[0] * 16u;
            m->meta_got = 0;
            p++;
            k--;
            if (m->meta_need > 0) {
                m->in_meta = 1;
            } else {
                m->icy_left = m->icy_int;
            }
        } else {
            uint32_t t = m->icy_left < k ? m->icy_left : k;
            in_put(m, p, t);
            m->icy_left -= t;
            p += t;
            k -= t;
        }
    }
}

// 1: bytes came; 0: none yet.
static int read_url(media_t *m) {
    if (m->h <= 0 || m->eof) {
        return 0;
    }
    uint32_t room = in_room(m);
    if (room < RD_BYTES) {
        return 0;
    }
    int k = moy_httpc_read_wait(m->h, m->rd, RD_BYTES, MOY_MEDIA_WAIT_MS);
    if (k == -MOY_HTTPC_EAGAIN) {
        return 0;
    }
    if (k <= 0) {
        m->eof = 1;
        m->err = k < 0 ? -k : 0;
        conn_close(m);
        return 1;
    }
    demux(m, m->rd, (uint32_t)k);
    return 1;
}

static void win_fill(media_t *m) {
    if (m->win_off >= MOY_MEDIA_WIN_MIN || (m->win_n == 0 && m->win_off > 0)) {
        memmove(m->win, m->win + m->win_off, m->win_n);
        m->win_off = 0;
    }
    uint32_t space = MOY_MEDIA_WIN_BYTES - m->win_off - m->win_n;
    uint32_t r = m->in_r, avail = LOAD(&m->in_w) - r;
    uint32_t k = avail < space ? avail : space;
    if (k == 0) {
        return;
    }
    uint32_t at = r & IN_MASK, first = MOY_MEDIA_IN_BYTES - at;
    if (first > k) {
        first = k;
    }
    uint8_t *dst = m->win + m->win_off + m->win_n;
    memcpy(dst, m->in + at, first);
    memcpy(dst + first, m->in, k - first);
    m->win_n += k;
    STORE(&m->in_r, r + k);
}

static void consume(media_t *m, uint32_t k) {
    k = k < m->win_n ? k : m->win_n;
    m->win_off += k;
    m->win_n -= k;
}

static uint32_t out_room(const media_t *m) {
    return MOY_MEDIA_PCM_FRAMES - (m->out_w - LOAD(&m->out_r));
}

static void put_frame(media_t *m, uint32_t w, int32_t l, int32_t r) {
    if (m->out_ch == 2) {
        m->out[2u * (w & OUT_MASK)] = (int16_t)l;
        m->out[2u * (w & OUT_MASK) + 1u] = (int16_t)r;
    } else {
        m->out[w & OUT_MASK] = (int16_t)l;
    }
}

// A decoded frame resampled into the decoded ring in the output's channels
// (a mono output takes the mean of a stereo stream's two, a stereo one a mono
// stream's one twice): averaged into each output frame going down, linear
// between two going up, a frame's channels apart. At the stream's own rate
// each frame is copied.
static void resample(media_t *m, const int16_t *pcm, int frames, int ch, int hz) {
    if (hz != m->rs_hz) {
        m->rs_hz = hz;
        m->rs_inc = (uint32_t)(((uint64_t)(uint32_t)m->out_rate << 16) / (uint32_t)hz);
        m->rs_step = (uint32_t)(((uint64_t)(uint32_t)hz << 16) / (uint32_t)m->out_rate);
        m->rs_pos = 0;
        m->rs_acc[0] = m->rs_acc[1] = 0;
        m->rs_cnt = 0;
    }
    uint32_t w = m->out_w;
    for (int i = 0; i < frames; i++) {
        int32_t x[2];
        if (ch == 2) {
            x[0] = pcm[2 * i];
            x[1] = pcm[2 * i + 1];
            if (m->out_ch == 1) {
                x[0] = (x[0] + x[1]) >> 1;
            }
        } else {
            x[0] = x[1] = pcm[i];
        }
        if (m->rs_inc == ONE) {
            put_frame(m, w++, x[0], x[1]);
        } else if (m->rs_inc < ONE) {
            m->rs_acc[0] += x[0];
            m->rs_acc[1] += x[1];
            m->rs_cnt++;
            m->rs_pos += m->rs_inc;
            if (m->rs_pos >= ONE) {
                m->rs_pos -= ONE;
                put_frame(m, w++, m->rs_acc[0] / m->rs_cnt, m->rs_acc[1] / m->rs_cnt);
                m->rs_acc[0] = m->rs_acc[1] = 0;
                m->rs_cnt = 0;
            }
        } else {
            while (m->rs_pos < ONE) {
                int32_t l = m->rs_prev[0] + (int32_t)(((int64_t)(x[0] - m->rs_prev[0]) * m->rs_pos) >> 16);
                int32_t r = m->rs_prev[1] + (int32_t)(((int64_t)(x[1] - m->rs_prev[1]) * m->rs_pos) >> 16);
                put_frame(m, w++, l, r);
                m->rs_pos += m->rs_step;
            }
            m->rs_pos -= ONE;
            m->rs_prev[0] = x[0];
            m->rs_prev[1] = x[1];
        }
    }
    m->decoded += w - m->out_w;
    m->e_dec += w - m->out_w;
    STORE(&m->out_w, w);
}

// One frame: 1 when bytes moved, 0 when it waits (for bytes or for room), -1
// when the entry's bytes are spent.
static int decode_one(media_t *m) {
    if (out_room(m) < m->out_need) {
        return 0;
    }
    win_fill(m);
    int last = m->eof && LOAD(&m->in_w) == m->in_r;
    const uint8_t *w = m->win + m->win_off;
    if (m->skip > 0) {
        uint32_t k = m->skip < m->win_n ? m->skip : m->win_n;
        consume(m, k);
        m->skip -= k;
        return k > 0 ? 1 : (last ? -1 : 0);
    }
    if (!m->head_seen) {
        if (m->win_n < 10 && !last) {
            return 0;
        }
        m->head_seen = 1;
        if (m->win_n >= 10 && w[0] == 'I' && w[1] == 'D' && w[2] == '3') {
            m->skip = 10u + ((uint32_t)(w[6] & 0x7f) << 21 | (uint32_t)(w[7] & 0x7f) << 14
                             | (uint32_t)(w[8] & 0x7f) << 7 | (uint32_t)(w[9] & 0x7f))
                      + ((w[5] & 0x10) ? 10u : 0u);
        }
        return 1;
    }
    if (m->win_n == 0) {
        return last ? -1 : 0;
    }
    if (m->win_n < MOY_MEDIA_WIN_MIN && !last) {
        return 0;
    }
    mp3dec_frame_info_t info;
    int s = mp3dec_decode_frame(&m->dec, w, (int)m->win_n, m->pcm, &info);
    if (info.frame_bytes <= 0) {
        consume(m, m->win_n);
        return last ? -1 : 1;
    }
    consume(m, (uint32_t)info.frame_bytes);
    if (s > 0 && info.hz > 0) {
        m->rate = info.hz;
        m->channels = info.channels;
        m->kbps = info.bitrate_kbps;
        m->e_used += (uint32_t)info.frame_bytes;
        resample(m, m->pcm, s, info.channels, info.hz);
        if (!m->played) {
            m->played = 1;
            LOCK(m);
            m->list_played = 1;
            UNLOCK(m);
        }
    }
    return 1;
}

// One pass: commands, the connection, a few frames. 1 when it did work.
static int step(media_t *m) {
    LOCK(m);
    int stop = m->stop, moved = m->gen != m->seen;
    if (!stop && m->kind == MOY_MEDIA_FEED) {
        m->eof = m->fed_end;
    }
    UNLOCK(m);
    if (stop) {
        if (m->idle) {
            return 0;
        }
        conn_close(m);
        LOCK(m);
        m->state = MOY_MEDIA_IDLE;
        m->idle = 1;
        UNLOCK(m);
        return 1;
    }
    if (moved) {
        begin(m);
        return 1;
    }
    int st = m->state;
    if (st == MOY_MEDIA_OPENING) {
        return open_url(m);
    }
    if (st != MOY_MEDIA_PLAYING) {
        return 0;
    }
    int work = m->kind == MOY_MEDIA_URL ? read_url(m) : 0;
    for (int i = 0; i < 4; i++) {
        int r = decode_one(m);
        if (r < 0) {
            advance(m, m->err);
            return 1;
        }
        if (r == 0) {
            break;
        }
        work = 1;
    }
    return work;
}

int moy_media_run(void) {
    media_t *m = s_m;
    int any = 0;
    for (int i = 0; m != NULL && i < 256; i++) {
        if (!step(m)) {
            break;
        }
        any = 1;
    }
    return any;
}

#ifdef MEDIA_BOARD
static void task(void *arg) {
    media_t *m = arg;
    for (;;) {
        if (!step(m)) {
            int st = m->state;
            int busy = !m->idle && (st == MOY_MEDIA_OPENING || st == MOY_MEDIA_PLAYING);
            ulTaskNotifyTake(pdTRUE, busy ? pdMS_TO_TICKS(10) : portMAX_DELAY);
        }
    }
}
#endif

// -- open and close ---------------------------------------------------------------------

int moy_media_open(uint32_t owner, int rate, int channels) {
    if (owner == 0u) {
        return -MOY_MEDIA_EINVAL;
    }
    if (s_m != NULL && s_m->owner == MOY_MEDIA_ORPHAN) {
        moy_media_close_vm();
    }
    if (s_m != NULL) {
        return s_m->owner == owner ? 0 : -MOY_MEDIA_EBUSY;
    }
#ifdef MEDIA_BOARD
    moy_aud_out_start();
    rate = moy_aud_out_rate();
    channels = moy_aud_out_channels();
#endif
    if (!moy_media_present()) {
        return -MOY_MEDIA_ENODEV;
    }
    if (rate < 8000 || rate > 96000 || channels < 1 || channels > 2) {
        return -MOY_MEDIA_EINVAL;
    }
    uint32_t share = share_of(channels);
    if (moy_share_take(MOY_SHARE_MEDIA, share) != MOY_PLAY_OK) {
        return -MOY_MEDIA_ENOSPC;
    }
    media_t *m = block_alloc(sizeof(media_t) + MOY_MEDIA_PCM_FRAMES * (size_t)channels * sizeof(int16_t));
    if (m == NULL) {
        moy_share_give(MOY_SHARE_MEDIA, share);
        return -MOY_MEDIA_ENOMEM;
    }
    m->owner = owner;
    m->share = share;
    m->out = (int16_t *)(m + 1);
    m->out_rate = rate;
    m->out_ch = channels;
    m->ramp = 64 * 22050 / rate;
    m->out_need = (uint32_t)(1152u * (uint32_t)rate / 8000u + 4u);
    m->entry = -1;
    m->cur = -1;
    m->volume = MOY_MEDIA_VOLUME;
    m->state = MOY_MEDIA_IDLE;
    mp3dec_init(&m->dec);
#ifdef MEDIA_BOARD
    m->mtx = xSemaphoreCreateMutexStatic(&m->mtx_buf);
    m->tcb = heap_caps_calloc(1, sizeof(StaticTask_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    m->stack = heap_caps_malloc(MOY_MEDIA_STACK, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (m->tcb != NULL && m->stack != NULL) {
        m->task = xTaskCreateStaticPinnedToCore(task, "moy_media", MOY_MEDIA_STACK / sizeof(StackType_t),
                                                m, MOY_MEDIA_PRIO, m->stack, m->tcb, 0);
    }
    if (m->mtx == NULL || m->task == NULL) {
        heap_caps_free(m->tcb);
        heap_caps_free(m->stack);
        block_free(m);
        moy_share_give(MOY_SHARE_MEDIA, share);
        return -MOY_MEDIA_ENOMEM;
    }
    moy_aud_lock();
    s_m = m;
    moy_aud_unlock();
    moy_aud_bg(moy_media_mix);
#else
    s_m = m;
#endif
    return 0;
}

#ifdef MEDIA_BOARD
// The task ended and its memory freed. Suspended first, and deleted once core 0
// has switched away from it, so the delete is immediate and nothing defers a
// touch of the TCB to the idle task (vTaskDeleteWithCaps does the same with
// eTaskGetState, which the S3s would carry in IRAM).
static void task_end(media_t *m) {
    vTaskSuspend(m->task);
    for (int i = 0; i < 100 && xTaskGetCurrentTaskHandleForCore(0) == m->task; i++) {
        vTaskDelay(1);
    }
    vTaskDelete(m->task);
    heap_caps_free(m->tcb);
    heap_caps_free(m->stack);
}
#endif

static void reap(media_t *m) {
#ifdef MEDIA_BOARD
    task_end(m);                        // idle: it waits on its notification and holds nothing
    moy_aud_bg(NULL);
    moy_aud_lock();
    s_m = NULL;
    moy_aud_unlock();
    vSemaphoreDelete(m->mtx);
#else
    s_m = NULL;
#endif
    uint32_t share = m->share;
    conn_close(m);
    block_free(m);
    moy_share_give(MOY_SHARE_MEDIA, share);
}

int moy_media_close(uint32_t owner) {
    media_t *m = mine(owner);
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    if (!m->stop) {
        moy_media_stop(owner);
    }
    if (!m->idle) {
        return -MOY_MEDIA_EAGAIN;
    }
    reap(m);
    return 0;
}

int moy_media_close_vm(void) {
    media_t *m = s_m;
    if (m == NULL) {
        return 0;
    }
    if (m->owner != MOY_MEDIA_ORPHAN) {
        moy_media_stop(m->owner);
        m->owner = MOY_MEDIA_ORPHAN;
    }
#ifdef MEDIA_BOARD
    for (int i = 0; i < 200 && !m->idle; i++) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
#else
    moy_media_run();
#endif
    if (!m->idle) {
        return -MOY_MEDIA_EAGAIN;
    }
    reap(m);
    return 0;
}

// -- the pull ------------------------------------------------------------------------------

void moy_media_mix(int16_t *out, int n, int console, int duck) {
    media_t *m = s_m;
    if (m == NULL) {
        return;
    }
    uint32_t fg = LOAD(&m->flush_gen);
    if (fg != m->flush_seen) {
        m->flush_seen = fg;
        STORE(&m->out_r, LOAD(&m->flush_at));
        m->started = 0;
    }
    if (m->paused || m->muted) {
        return;
    }
    int32_t target = m->volume * 32767 / 100;
    target = target * (console < 0 ? 0 : console > 7 ? 7 : console) / 7;
    if (duck) {
        target = (target * MOY_MEDIA_DUCK_Q15) >> 15;
    }
    const int32_t ramp = m->ramp;       // about 23 ms from silence to full
    int32_t g = m->gain;
    uint32_t r = m->out_r, w = LOAD(&m->out_w), took = 0, short_ = 0;
    for (int i = 0; i < n; i++) {
        g = g < target ? (g + ramp < target ? g + ramp : target)
                       : (g - ramp > target ? g - ramp : target);
        if (r == w) {
            short_++;
            continue;
        }
        if (m->out_ch == 2) {
            const int16_t *v = &m->out[2u * (r & OUT_MASK)];
            int32_t l = out[2 * i] + ((v[0] * g) >> 15);
            int32_t rr = out[2 * i + 1] + ((v[1] * g) >> 15);
            out[2 * i] = (int16_t)(l > 32767 ? 32767 : l < -32768 ? -32768 : l);
            out[2 * i + 1] = (int16_t)(rr > 32767 ? 32767 : rr < -32768 ? -32768 : rr);
        } else {
            int32_t o = out[i] + ((m->out[r & OUT_MASK] * g) >> 15);
            out[i] = (int16_t)(o > 32767 ? 32767 : o < -32768 ? -32768 : o);
        }
        r++;
        took++;
    }
    STORE(&m->out_r, r);
    m->gain = g;
    m->pulled += took;
    if (took > 0) {
        m->started = 1;
    }
    if (m->started && short_ > 0 && m->state == MOY_MEDIA_PLAYING) {
        m->starved += short_;
    }
}

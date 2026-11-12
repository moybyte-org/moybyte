// moy_media: the media service (moy_media.h has the contract).

#include "moy_kmem.h"

#include "moy_media.h"
#include "moy_lease.h"

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "minimp3.h"
#include "moy_input.h"
#include "moy_log.h"
#include "moy_notes.h"
#include "moy_play.h"
#include "moy_vol.h"

// The frame's jobs (native/moy_kernel/moy_loop.h); weak, for an image or a
// harness without the loop: open() then refuses (-ENODEV).
#include "moy_loop.h"
#pragma weak moy_loop_job
#pragma weak moy_loop_job_drop
// The kernel's notification queue (moy_notes.h); weak, for an image without it.
#pragma weak moy_notes_system


// MOY_MEDIA_CLIENT 0: the image has no HTTP client (the browser's), so a
// URL's bytes are fed as a file's are, by the face from the page's fetch.
// MOY_MEDIA_MIX 1: moy_audio's mix is in the image (a board's, the
// browser's), and open() hands it the pull; the host's face attaches it.
#ifndef MOY_MEDIA_CLIENT
#define MOY_MEDIA_CLIENT 1
#endif
#ifdef MOY_ON_BOARD
#undef MOY_MEDIA_MIX
#define MOY_MEDIA_MIX 1
#endif
#ifndef MOY_MEDIA_MIX
#define MOY_MEDIA_MIX 0
#endif

#if MOY_MEDIA_CLIENT
#include "moy_net.h"
#include "moy_ota.h"
#endif
#if MOY_MEDIA_MIX
#include "moy_aud.h"
#endif
#ifdef MOY_ON_BOARD
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#elif !defined(__EMSCRIPTEN__)
#include <pthread.h>
#endif

#define ONE 65536u
#define IN_MASK (MOY_MEDIA_IN_BYTES - 1u)
#define OUT_MASK (MOY_MEDIA_PCM_FRAMES - 1u)
#define NO_TITLE 0xffffu
#define RD_BYTES 4096u
#define META_MAX (255u * 16u)
#define ICY_INT_MAX (1u << 20)             // the longest metadata interval taken
#define NO_SEEK 0xffffffffu
#define READ_CHUNK (8u * 1024u)         // one read of a file, under the lock

_Static_assert((MOY_MEDIA_IN_BYTES & IN_MASK) == 0, "the stream buffer is a power of two");
_Static_assert((MOY_MEDIA_PCM_FRAMES & OUT_MASK) == 0, "the decoded ring is a power of two");

typedef struct {
    uint32_t pid;                   // the service's own, spawned at its start
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
    int fed_err;                    // why the feed ended early (positive errno), 0
    char title[MOY_MEDIA_TEXT_MAX], station[MOY_MEDIA_TEXT_MAX];
    uint32_t text_gen;
    int list_played;                // an entry of this list decoded a frame
    int page;                       // the page holds songs the VFS does not
    uint32_t face_gen;              // the armed generation the face feeds, 0 none
    uint32_t armed_at;              // the byte the armed generation starts at
    uint32_t seek_req, seek_dec;    // a seek's byte and frames, NO_SEEK for none
    char song[MOY_MEDIA_TEXT_MAX], artist[MOY_MEDIA_TEXT_MAX];
    uint32_t client;                // the pid whose command set what plays, 0 none
    char who[MOY_MEDIA_TEXT_MAX];   // its name, as the face gave it
    char label[MOY_MEDIA_SRC_MAX];  // the client's own words for what plays

    // the end: the frame's job's
    int ending;                     // the list is over: the tail plays out
    int end_state, end_err;         // how it ended, for moy_media_last
    uint32_t end_pulled;            // the pull's count when the tail was last read

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
    volatile uint32_t e_head;       // its tag's bytes before the first frame

    // the frame's job: the file it reads (the VM's thread)
    moy_vol_file_t *f;
    uint32_t f_gen;                 // the armed generation it settled, 0 none
    uint32_t f_left;                // the file's bytes not yet read
    char path[MOY_MEDIA_SRC_MAX];

    // the stream buffer: one writer (the task, or the VM's feed under the lock), one reader
    volatile uint32_t in_w, in_r;
    uint8_t in[MOY_MEDIA_IN_BYTES];

    // the decoder: its state internal SRAM where open() could take it (sram)
    mp3dec_t *dec;
    uint32_t sram;                  // the run's bytes in internal SRAM: the state, the stack
    uint32_t dec_n, dec_sum;        // the meter's window: frames, their decode µs
    volatile uint32_t dec_us;       // the last full window's mean decode, µs
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

    // the media keys: a reader of MEDIA records on keys_t, -1 none
    moy_input_t *keys_t;
    int keys_r;

    uint8_t rd[RD_BYTES];
    char meta[META_MAX + 1];
    char url[MOY_MEDIA_SRC_MAX];

    moy_mutex_t mtx;                    // a client's task beside the frame's job
#ifdef MOY_ON_BOARD
    TaskHandle_t task;
    StaticTask_t *tcb;                  // internal, as a TCB must be
    StackType_t *stack;                 // MOY_MEDIA_STACK, internal SRAM where open() could take it
#endif
} media_t;

static media_t *s_m;
static moy_media_end_t s_end;           // the last run's end

static int copy_text(const char *s, char *out, size_t cap);
static moy_input_t *s_keys_t;           // the table the media keys come on; NULL the kernel's

#define LOCK(m) moy_mutex_lock(&(m)->mtx)
#define UNLOCK(m) moy_mutex_unlock(&(m)->mtx)
#ifdef MOY_ON_BOARD
#define WAKE(m) xTaskNotifyGive((m)->task)
#else
#define WAKE(m) ((void)(m))
#endif

// The service's life: a teardown (close) and
// the frame's job run one at a time, so a teardown on any task frees nothing
// the job reads.
static moy_mutex_t s_life = MOY_MUTEX_INIT;
#define LIFE_LOCK() moy_mutex_lock(&s_life)
#define LIFE_UNLOCK() moy_mutex_unlock(&s_life)

#define LOAD(p) __atomic_load_n((p), __ATOMIC_ACQUIRE)
#define STORE(p, v) __atomic_store_n((p), (v), __ATOMIC_RELEASE)

static void *block_alloc(size_t n) {
    return moy_kalloc(n, MOY_KMEM_PSRAM | MOY_KMEM_ZERO);
}

static void block_free(void *p) {
    moy_kfree(p);
}

// `n` zeroed bytes of internal SRAM, counted in the run's `sram`, where the heap
// keeps MOY_MEDIA_SRAM_FLOOR free beside them; NULL where it cannot (the
// caller takes PSRAM) and on a build with no board heap.
static void *sram_take(media_t *m, size_t n) {
#ifdef MOY_ON_BOARD
    if (heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) < n + MOY_MEDIA_SRAM_FLOOR) {
        return NULL;
    }
    void *p = moy_kalloc(n, MOY_KMEM_INTERNAL | MOY_KMEM_ZERO);
    if (p != NULL) {
        m->sram += (uint32_t)n;
    }
    return p;
#else
    (void)m;
    (void)n;
    return NULL;
#endif
}

static uint32_t share_of(int ch) {
    return (uint32_t)sizeof(media_t) + MOY_MEDIA_PCM_FRAMES * (uint32_t)ch * sizeof(int16_t)
           + (uint32_t)sizeof(mp3dec_t) + MOY_MEDIA_STACK + MOY_MEDIA_CONN;
}

uint32_t moy_media_share(void) {
    media_t *m = s_m;
    if (m != NULL) {
        return m->share;
    }
#ifdef MOY_ON_BOARD
    return share_of(moy_aud_out_channels());
#else
    return share_of(1);
#endif
}

int moy_media_present(void) {
#ifdef MOY_ON_BOARD
    int st = moy_aud_out_state(NULL);
    return st != MOY_AUD_OUT_FAILED && st != MOY_AUD_OUT_ABSENT;
#else
    return 1;
#endif
}

uint32_t moy_media_pid(void) {
    media_t *m = s_m;
    return m != NULL ? m->pid : 0u;
}

void moy_media_last(moy_media_end_t *out) {
    *out = s_end;
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

// -- the tag (ID3v2) ---------------------------------------------------------------

static uint32_t be32(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

static uint32_t syncsafe(const uint8_t *p) {
    return (uint32_t)(p[0] & 0x7f) << 21 | (uint32_t)(p[1] & 0x7f) << 14
           | (uint32_t)(p[2] & 0x7f) << 7 | (uint32_t)(p[3] & 0x7f);
}

// Code point `c` at out[o] as UTF-8: the new length, or 0 when it does not
// fit before the NUL.
static size_t put_cp(char *out, size_t o, size_t cap, uint32_t c) {
    uint8_t b[4];
    size_t k;
    if (c < 0x80) {
        b[0] = (uint8_t)c;
        k = 1;
    } else if (c < 0x800) {
        b[0] = (uint8_t)(0xc0 | c >> 6);
        b[1] = (uint8_t)(0x80 | (c & 0x3f));
        k = 2;
    } else if (c < 0x10000) {
        b[0] = (uint8_t)(0xe0 | c >> 12);
        b[1] = (uint8_t)(0x80 | ((c >> 6) & 0x3f));
        b[2] = (uint8_t)(0x80 | (c & 0x3f));
        k = 3;
    } else {
        b[0] = (uint8_t)(0xf0 | c >> 18);
        b[1] = (uint8_t)(0x80 | ((c >> 12) & 0x3f));
        b[2] = (uint8_t)(0x80 | ((c >> 6) & 0x3f));
        b[3] = (uint8_t)(0x80 | (c & 0x3f));
        k = 4;
    }
    if (o + k >= cap) {
        return 0;
    }
    memcpy(out + o, b, k);
    return o + k;
}

// A text frame's body (its encoding byte, then its text) into `out` as
// UTF-8, to its first NUL: ISO-8859-1, UTF-16 with its BOM, UTF-16BE, UTF-8.
static void id3_text(const uint8_t *p, size_t n, char *out, size_t cap) {
    out[0] = '\0';
    if (n < 1 || cap == 0) {
        return;
    }
    int enc = p[0];
    p++;
    n--;
    size_t o = 0;
    if (enc == 3) {
        size_t k = 0;
        while (k < n && p[k] != 0) {
            k++;
        }
        to_utf8((const char *)p, k, out, cap);
        return;
    }
    if (enc == 0) {
        for (size_t i = 0; i < n && p[i] != 0; i++) {
            size_t w = put_cp(out, o, cap, p[i]);
            if (w == 0) {
                break;
            }
            o = w;
        }
    } else if (enc == 1 || enc == 2) {
        int be = enc == 2;
        size_t i = 0;
        if (enc == 1 && n >= 2 && ((p[0] == 0xff && p[1] == 0xfe) || (p[0] == 0xfe && p[1] == 0xff))) {
            be = p[0] == 0xfe;
            i = 2;
        }
        for (; i + 1 < n; i += 2) {
            uint32_t c = be ? (uint32_t)p[i] << 8 | p[i + 1] : (uint32_t)p[i + 1] << 8 | p[i];
            if (c == 0) {
                break;
            }
            if (c >= 0xd800 && c < 0xdc00 && i + 3 < n) {
                uint32_t d = be ? (uint32_t)p[i + 2] << 8 | p[i + 3] : (uint32_t)p[i + 3] << 8 | p[i + 2];
                if (d >= 0xdc00 && d < 0xe000) {
                    c = 0x10000u + ((c - 0xd800u) << 10) + (d - 0xdc00u);
                    i += 2;
                } else {
                    c = 0xfffd;
                }
            } else if (c >= 0xd800 && c < 0xe000) {
                c = 0xfffd;
            }
            size_t w = put_cp(out, o, cap, c);
            if (w == 0) {
                break;
            }
            o = w;
        }
    }
    out[o] = '\0';
}

uint32_t moy_media_id3(const uint8_t *p, size_t n, char *title, char *artist, size_t cap) {
    if (cap > 0) {
        title[0] = '\0';
        artist[0] = '\0';
    }
    if (n < 10 || p[0] != 'I' || p[1] != 'D' || p[2] != '3') {
        return 0;
    }
    int ver = p[3], flags = p[5];
    uint32_t size = syncsafe(p + 6);
    uint32_t total = 10u + size + ((flags & 0x10) ? 10u : 0u);
    if (ver < 2 || ver > 4 || (flags & 0x80) || cap == 0) {
        return total;                       // unsynchronised, or unknown: not read
    }
    size_t end = 10u + (size_t)size < n ? 10u + (size_t)size : n;
    size_t at = 10;
    if ((flags & 0x40) && ver > 2 && at + 4 <= end) {
        at += ver == 3 ? 4u + be32(p + at) : syncsafe(p + at);
    }
    size_t hn = ver == 2 ? 6 : 10;
    while (at + hn <= end && p[at] != 0) {
        const uint8_t *h = p + at;
        uint32_t fs = ver == 2 ? (uint32_t)h[3] << 16 | (uint32_t)h[4] << 8 | h[5]
                      : ver == 3 ? be32(h + 4) : syncsafe(h + 4);
        size_t body = at + hn;
        if (fs > end - body) {
            break;                          // past what is here
        }
        int plain = ver == 2 || h[9] == 0;  // no compression, encryption or unsync
        char *dst = NULL;
        if (ver == 2 ? memcmp(h, "TT2", 3) == 0 : memcmp(h, "TIT2", 4) == 0) {
            dst = title;
        } else if (ver == 2 ? memcmp(h, "TP1", 3) == 0 : memcmp(h, "TPE1", 4) == 0) {
            dst = artist;
        }
        if (dst != NULL && plain) {
            id3_text(p + body, fs, dst, cap);
        }
        at = body + fs;
    }
    return total;
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

static void clear_locked(media_t *m) {
    m->n = 0;
    m->pool_n = 0;
    m->entry = -1;
    m->gen++;
    m->soft = 0;
    m->seek_req = NO_SEEK;
    m->list_played = 0;
}

uint32_t moy_media_take(uint32_t client, const char *who, const char *label) {
    media_t *m = s_m;
    if (m == NULL || client == 0u) {
        return 0;
    }
    LOCK(m);
    uint32_t was = m->client;
    int live = !m->stop && (m->state == MOY_MEDIA_OPENING || m->state == MOY_MEDIA_PLAYING);
    m->client = client;
    copy_text(who != NULL ? who : "", m->who, sizeof(m->who));
    copy_text(label != NULL ? label : "", m->label, sizeof(m->label));
    m->text_gen++;
    UNLOCK(m);
    return live && was != 0u && was != client ? was : 0u;
}

int moy_media_clear(void) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    LOCK(m);
    clear_locked(m);
    UNLOCK(m);
    WAKE(m);
    return 0;
}

int moy_media_add(const char *src, const char *title) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    LOCK(m);
    int rc = add_locked(m, src, strlen(src), title, title != NULL ? strlen(title) : 0);
    UNLOCK(m);
    return rc;
}

int moy_media_playlist(const char *text, size_t n, const char *base) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    LOCK(m);
    int rc = parse_locked(m, text, n, base);
    UNLOCK(m);
    return rc;
}

int moy_media_go(int i) {
    media_t *m = s_m;
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
    m->seek_req = NO_SEEK;
    m->stop = 0;
    m->idle = 0;
    m->muted = 0;
    m->err = 0;
    UNLOCK(m);
    WAKE(m);
    return 0;
}

int moy_media_stop(void) {
    media_t *m = s_m;
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

int moy_media_pause(int on) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    m->paused = on ? 1 : 0;
    WAKE(m);
    return 0;
}

int moy_media_volume(int v) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    m->volume = v < 0 ? 0 : v > 100 ? 100 : v;
    return 0;
}

// -- files: the store's volume seam, on the VM's thread ---------------------------

// The file at `path` opened (held where `held`), sized, and placed at byte
// `at`: 0, or an errno with nothing left open.
static int vol_open(const char *path, int held, uint32_t at, moy_vol_file_t **f, uint32_t *size) {
    moy_vol_t v;
    const char *r;
    *f = NULL;
    *size = 0;
    int g = moy_vol_gate_enter(path);
    int rc = moy_vol_at(path, &v, &r);
    if (rc == 0) {
        rc = moy_vol_open(&v, r, MOY_VOL_READ | (held ? MOY_VOL_HELD : 0), f);
        if (rc != 0) {
            *f = NULL;
        }
    }
    if (rc == 0) {
        rc = moy_vol_size(*f, size);
    }
    if (rc == 0 && at > 0) {
        rc = moy_vol_seek(*f, at < *size ? at : *size);
    }
    if (rc != 0 && *f != NULL) {
        moy_vol_close(*f);
        *f = NULL;
    }
    moy_vol_gate_leave(g);
    return rc;
}

static void vol_close(const char *path, moy_vol_file_t *f) {
    int g = moy_vol_gate_enter(path);
    moy_vol_close(f);
    moy_vol_gate_leave(g);
}

// Up to `cap` bytes of the file at `path` from byte `at`: 0 or an errno,
// `*got` read and the file's size in `*size`.
static int vol_read_at(const char *path, uint32_t at, uint8_t *buf, size_t cap, size_t *got,
                       uint32_t *size) {
    moy_vol_file_t *f;
    *got = 0;
    int rc = vol_open(path, 0, at, &f, size);
    if (rc != 0) {
        return rc;
    }
    int g = moy_vol_gate_enter(path);
    while (rc == 0 && *got < cap) {
        size_t k = 0;
        rc = moy_vol_read(f, buf + *got, cap - *got, &k);
        if (k == 0) {
            break;
        }
        *got += k;
    }
    int rc2 = moy_vol_close(f);
    moy_vol_gate_leave(g);
    return rc != 0 ? rc : rc2;
}

int moy_media_stat(const char *path) {
    moy_vol_t v;
    const char *r;
    moy_vol_stat_t st;
    int g = moy_vol_gate_enter(path);
    int e = moy_vol_at(path, &v, &r);
    if (e == 0) {
        e = moy_vol_stat(&v, r, &st);
    }
    moy_vol_gate_leave(g);
    return e == 0 && st.is_dir ? MOY_MEDIA_EINVAL : e;
}

#if MOY_MEDIA_CLIENT
#if __has_include("moy_fetch.h")
#include "moy_fetch.h"
#else
int moy_fetch_tls(void);
#endif
#pragma weak moy_fetch_tls
#endif

int moy_media_tls(void) {
#if MOY_MEDIA_CLIENT
    return moy_fetch_tls != NULL && moy_fetch_tls();
#else
    return 1;                           // the page's fetch verifies its own
#endif
}

int moy_media_play(const char *src, int held) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    size_t sn = strlen(src);
    if (sn == 0 || sn >= MOY_MEDIA_SRC_MAX) {
        return -MOY_MEDIA_EINVAL;
    }
    int rc;
    if (is_url(src)) {
        LOCK(m);
        clear_locked(m);
        rc = add_locked(m, src, sn, NULL, 0);
        UNLOCK(m);
    } else if (moy_media_list_kind(NULL, src, NULL, 0) != 0) {
        uint8_t *buf = moy_store_alloc(MOY_MEDIA_LIST_FILE);
        if (buf == NULL) {
            return -MOY_MEDIA_ENOMEM;
        }
        size_t n = 0;
        uint32_t size;
        rc = vol_read_at(src, 0, buf, MOY_MEDIA_LIST_FILE, &n, &size);
        if (rc == 0) {
            LOCK(m);
            clear_locked(m);
            rc = parse_locked(m, (const char *)buf, n, src);
            UNLOCK(m);
            rc = rc > 0 ? rc : -MOY_MEDIA_EINVAL;
        } else {
            rc = -rc;
        }
        moy_store_free(buf, MOY_MEDIA_LIST_FILE);
    } else {
        int e = moy_media_stat(src);
        if (e != 0 && !held) {
            return -e;
        }
        LOCK(m);
        clear_locked(m);
        rc = add_locked(m, src, sn, NULL, 0);
        UNLOCK(m);
    }
    if (rc < 0) {
        WAKE(m);
        return rc;
    }
    int n = m->n;
    rc = moy_media_go(0);
    return rc < 0 ? rc : n;
}

int moy_media_page(int on) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    LOCK(m);
    m->page = on ? 1 : 0;
    UNLOCK(m);
    return 0;
}

int moy_media_seek(uint32_t ms) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    int rc = 0;
    LOCK(m);
    uint32_t dec = m->e_dec, used = m->e_used, head = m->e_head, size = m->e_size;
    if (m->stop || m->cur < 0 || m->cur >= m->n) {
        rc = -MOY_MEDIA_EINVAL;
    } else if (is_url(m->pool + m->e_src[m->cur])) {
        rc = -MOY_MEDIA_ESPIPE;
    } else if (m->gen != m->seen || size == 0 || used == 0 || dec == 0) {
        rc = -MOY_MEDIA_EAGAIN;
    } else {
        uint64_t frames = (uint64_t)ms * (uint32_t)m->out_rate / 1000u;
        uint64_t byte = head + frames * used / dec;
        if (byte > size) {
            byte = size;
            frames = (uint64_t)(size > head ? size - head : 0) * dec / used;
        }
        m->seek_req = (uint32_t)byte;
        m->seek_dec = (uint32_t)frames;
        m->entry = m->cur;
        m->gen++;
        m->soft = 0;
        m->idle = 0;
        m->muted = 0;
        m->err = 0;
    }
    UNLOCK(m);
    if (rc == 0) {
        WAKE(m);
    }
    return rc;
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

// A stream's bytes, its ICY metadata taken out, into the stream buffer (the
// connection's on a board, the face's feed in the browser): the length of
// the last StreamTitle a metadata block carried, copied into `title`, else -1.
static int demux(media_t *m, const uint8_t *p, uint32_t k, char *title, size_t cap) {
    int tn = -1;
    while (k > 0) {
        if (m->icy_int == 0) {
            in_put(m, p, k);
            return tn;
        }
        if (m->in_meta) {
            uint32_t t = m->meta_need - m->meta_got;
            t = t < k ? t : k;
            memcpy(m->meta + m->meta_got, p, t);
            m->meta_got += t;
            p += t;
            k -= t;
            if (m->meta_got == m->meta_need) {
                int n = moy_media_icy_title(m->meta, m->meta_got, title, cap);
                if (n >= 0) {
                    tn = n;
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
    return tn;
}

int moy_media_feed(uint32_t gen, const void *buf, size_t n) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    uint32_t k = 0;
    char title[MOY_MEDIA_TEXT_MAX];
    int tn = -1;
    LOCK(m);
    if (gen != 0 && m->armed == gen && m->face_gen == gen && !m->fed_end) {
        k = in_room(m);
        if (k > n) {
            k = (uint32_t)n;
        }
        tn = demux(m, buf, k, title, sizeof(title));
    }
    UNLOCK(m);
    if (tn >= 0) {
        set_text(m, m->title, title, (size_t)tn);
    }
    if (k > 0) {
        WAKE(m);
    }
    return (int)k;
}

int moy_media_feed_icy(uint32_t gen, uint32_t metaint, const char *station) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    LOCK(m);
    int ours = gen != 0 && m->armed == gen && m->face_gen == gen && !m->fed_end;
    if (ours) {
        m->icy_int = metaint <= ICY_INT_MAX ? metaint : 0;
        m->icy_left = m->icy_int;
        m->in_meta = 0;
    }
    UNLOCK(m);
    if (ours && station != NULL && station[0] != '\0') {
        set_text(m, m->station, station, strlen(station));
    }
    return 0;
}

int moy_media_feed_fail(uint32_t gen, int err) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    LOCK(m);
    if (gen != 0 && m->armed == gen && m->face_gen == gen && !m->fed_end) {
        m->fed_end = 1;
        m->fed_err = err > 0 ? err : 0;
    }
    UNLOCK(m);
    WAKE(m);
    return 0;
}

int moy_media_feed_end(uint32_t gen) {
    return moy_media_feed_fail(gen, 0);
}

int moy_media_feed_size(uint32_t gen, uint32_t size) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    LOCK(m);
    if (gen != 0 && m->armed == gen && m->face_gen == gen) {
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
    st->pid = m->pid;
    st->client = m->client;
    st->gen = m->seen;
    st->entry = m->cur;
    st->entries = m->n;
    int face = m->armed != 0 && m->armed == m->seen && m->face_gen == m->armed;
    st->kind = face ? MOY_MEDIA_FEED : m->kind;
    st->armed = face && !m->fed_end;
    st->feed_at = m->armed_at;
    st->rate = m->rate;
    st->channels = m->channels;
    st->kbps = m->kbps;
    st->volume = m->volume;
    st->err = m->err;
    st->text_gen = m->text_gen;
    st->idle = m->idle;
    st->sram = m->sram;
    st->dec_us = LOAD(&m->dec_us);
    uint32_t size = m->e_size;
    UNLOCK(m);
    uint32_t dec = m->e_dec, used = m->e_used;
    // a flush the pull has not taken yet drops what it held already
    uint32_t from = LOAD(&m->flush_gen) != m->flush_seen ? LOAD(&m->flush_at) : LOAD(&m->out_r);
    uint32_t held = LOAD(&m->out_w) - from;
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

int32_t moy_media_stamp(void) {
    moy_media_status_t s;
    if (!moy_media_status(&s)) {
        return -1;
    }
    uint32_t h = (uint32_t)s.state * 2u + (s.paused ? 1u : 0u);
    h = h * 31u + (uint32_t)s.entry;
    h = h * 31u + (uint32_t)s.volume;
    h = h * 31u + (uint32_t)s.err;
    h = h * 31u + s.text_gen;
    h = h * 31u + s.at_ms / 1000u;
    h = h * 31u + s.len_ms / 1000u;
    return (int32_t)(h & 0x3fffffffu);
}

// A process's name for the person: the OS log's, else its pid.
static const char *who_of(uint32_t pid, char *buf, size_t cap) {
    const char *n = moy_log_name(pid);
    if (n != NULL && *n != 0) {
        return n;
    }
    snprintf(buf, cap, "pid %u", (unsigned)pid);
    return buf;
}

int moy_media_tell(uint32_t was) {
    media_t *m = s_m;
    uint32_t client = m != NULL ? m->client : 0u;
    if (was == 0u || client == 0u || was == client) {
        return 0;
    }
    char label[MOY_MEDIA_TEXT_MAX], src[MOY_MEDIA_SRC_MAX], a[16], b[16];
    char summary[MOY_NOTES_SUMMARY + 1u], body[MOY_NOTES_BODY + 1u];
    moy_media_text(MOY_MEDIA_T_LABEL, label, sizeof label);
    const char *tab = strrchr(label, '\t');
    const char *name = tab != NULL ? tab + 1 : label;
    if (*name == 0) {
        moy_media_text(MOY_MEDIA_T_SRC, src, sizeof src);
        const char *slash = strrchr(src, '/');
        name = slash != NULL ? slash + 1 : src;
    }
    int sn = snprintf(summary, sizeof summary, "%s took over the music",
                      who_of(client, a, sizeof a));
    int bn = snprintf(body, sizeof body, "%s now plays in place of what %s played.", name,
                      who_of(was, b, sizeof b));
    if (sn < 0 || bn < 0 || moy_notes_system == NULL) {
        return 0;
    }
    moy_notes_system(summary, (size_t)sn < sizeof summary ? (size_t)sn : sizeof summary - 1u,
                     body, (size_t)bn < sizeof body ? (size_t)bn : sizeof body - 1u);
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
    int i = m->gen != m->seen ? m->entry : m->cur;     // a go() the task has not begun
    if (which == MOY_MEDIA_T_TITLE) {
        n = copy_text(m->title, out, cap);
    } else if (which == MOY_MEDIA_T_STATION) {
        n = copy_text(m->station, out, cap);
    } else if (which == MOY_MEDIA_T_SONG) {
        n = copy_text(m->song, out, cap);
    } else if (which == MOY_MEDIA_T_ARTIST) {
        n = copy_text(m->artist, out, cap);
    } else if (which == MOY_MEDIA_T_WHO) {
        n = copy_text(m->who, out, cap);
    } else if (which == MOY_MEDIA_T_LABEL) {
        n = copy_text(m->label, out, cap);
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
#if MOY_MEDIA_CLIENT
    if (m->h > 0) {
        moy_httpc_close(m->h);
    }
#endif
    m->h = 0;
}

// A URL's length in bytes: its Content-Length, or the size its feeder gave
// (feed_size); 0 for a live stream.
static uint32_t url_len(const media_t *m) {
#if MOY_MEDIA_CLIENT
    return m->clen;
#else
    return m->e_size;
#endif
}

// The entry moves on by itself: again for a URL that played and has retries
// left, else the next. A command since wins.
static void advance(media_t *m, int err) {
    m->err = err;
    conn_close(m);
    LOCK(m);
    if (m->gen == m->seen && !m->stop) {
        int again = m->kind == MOY_MEDIA_URL && url_len(m) == 0 && m->played
                    && m->retries < MOY_MEDIA_RETRIES;
        m->entry = again ? m->cur : m->cur + 1;
        m->gen++;
        m->soft = 1;
    }
    UNLOCK(m);
}

// Entry `e` begins: what the last one held dropped (the decoded tail too,
// unless the move was the task's own), the state OPENING for a URL. A seek
// begins the same file again at its byte, its place, length and texts kept.
static void begin(media_t *m) {
    conn_close(m);
    LOCK(m);
    int e = m->entry, soft = m->soft;
    uint32_t seek = m->seek_req;
    m->seek_req = NO_SEEK;
    m->seen = m->gen;
    m->armed = 0;
    m->fed_end = 0;
    m->fed_err = 0;
    STORE(&m->in_r, LOAD(&m->in_w));
    int kind = e >= 0 && e < m->n ? (is_url(m->pool + m->e_src[e]) ? MOY_MEDIA_URL : MOY_MEDIA_FILE) : 0;
    if (kind != MOY_MEDIA_FILE) {
        seek = NO_SEEK;
    }
    int again = e == m->cur && (soft || seek != NO_SEEK);
    m->cur = e;
    if (seek != NO_SEEK) {
        m->e_dec = m->seek_dec;
        m->e_used = seek > m->e_head ? seek - m->e_head : 0;
    } else if (kind != 0) {
        // a new entry's place; past the list's end the last one's stays
        m->e_size = 0;
        m->e_dec = 0;
        m->e_used = 0;
        m->e_head = 0;
    }
    m->armed_at = seek != NO_SEEK ? seek : 0;
    m->face_gen = kind == MOY_MEDIA_URL && !MOY_MEDIA_CLIENT ? m->seen : 0;
    if (kind == MOY_MEDIA_FILE || (kind == MOY_MEDIA_URL && !MOY_MEDIA_CLIENT)) {
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
        m->song[0] = '\0';
        m->artist[0] = '\0';
        m->text_gen++;
    }
    UNLOCK(m);
    m->kind = kind;
    m->retries = again && seek == NO_SEEK ? m->retries + 1 : 0;
    m->dial_ms = 0;
    m->played = 0;
    m->eof = 0;
    m->skip = 0;
    m->head_seen = seek != NO_SEEK;
    m->icy_int = 0;
    m->in_meta = 0;
    m->win_off = 0;
    m->win_n = 0;
    m->rs_hz = 0;
    mp3dec_init(m->dec);
    if (!soft) {
        STORE(&m->flush_at, LOAD(&m->out_w));
        STORE(&m->flush_gen, m->flush_gen + 1u);
    }
}

#if MOY_MEDIA_CLIENT
static int header_int(int h, const char *name) {
    char v[16];
    if (moy_httpc_header(h, name, v, sizeof(v)) <= 0) {
        return 0;
    }
    int x = 0;
    for (const char *p = v; *p >= '0' && *p <= '9'; p++) {
        x = x * 10 + (*p - '0');
        if (x > (int)ICY_INT_MAX) {
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

// 0 while the URL waits for the link (the task polls again), else 1. Nothing
// resolves its host before THE NETWORK'S GATE (moy_net.h) opens: the radio's
// "media" row dials the station (THE BOOK), and the entry waits for it from
// its begin, MOY_NET_DIAL_MS at most, then fails ENET (no network).
static int open_url(media_t *m) {
    int up = moy_net_await(&m->dial_ms);
    if (up == 0) {
        return 0;
    }
    if (up < 0) {
        advance(m, -up);
        return 1;
    }
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
    char title[MOY_MEDIA_TEXT_MAX];
    int tn = demux(m, m->rd, (uint32_t)k, title, sizeof(title));
    if (tn >= 0) {
        set_text(m, m->title, title, (size_t)tn);
    }
    return 1;
}

#else

// A fed URL is OPENING until its first bytes, or its feed's end, come.
static int open_url(media_t *m) {
    if (LOAD(&m->in_w) == m->in_r && !m->eof) {
        return 0;
    }
    m->state = MOY_MEDIA_PLAYING;
    return 1;
}

static int read_url(media_t *m) {
    (void)m;
    return 0;
}
#endif

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
        if (m->win_n >= 10 && w[0] == 'I' && w[1] == 'D' && w[2] == '3') {
            // the tag's frames are read once as much of it as the window holds is here
            uint32_t total = 10u + syncsafe(w + 6) + ((w[5] & 0x10) ? 10u : 0u);
            uint32_t want = total < MOY_MEDIA_WIN_BYTES ? total : MOY_MEDIA_WIN_BYTES;
            if (m->win_n < want && !last) {
                return 0;
            }
            char title[MOY_MEDIA_TEXT_MAX], artist[MOY_MEDIA_TEXT_MAX];
            moy_media_id3(w, m->win_n, title, artist, sizeof(title));
            LOCK(m);
            memcpy(m->song, title, sizeof(title));
            memcpy(m->artist, artist, sizeof(artist));
            m->text_gen++;
            UNLOCK(m);
            m->skip = total;
            m->e_head = total;
        }
        m->head_seen = 1;
        return 1;
    }
    if (m->win_n == 0) {
        return last ? -1 : 0;
    }
    if (m->win_n < MOY_MEDIA_WIN_MIN && !last) {
        return 0;
    }
    mp3dec_frame_info_t info;
#ifdef MOY_ON_BOARD
    int64_t t0 = moy_now_us();
#endif
    int s = mp3dec_decode_frame(m->dec, w, (int)m->win_n, m->pcm, &info);
#ifdef MOY_ON_BOARD
    if (s > 0) {
        m->dec_sum += (uint32_t)(moy_now_us() - t0);
        if (++m->dec_n == MOY_MEDIA_METER_FRAMES) {
            STORE(&m->dec_us, m->dec_sum / MOY_MEDIA_METER_FRAMES);
            m->dec_n = 0;
            m->dec_sum = 0;
        }
    }
#endif
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
    if (!stop && (m->kind == MOY_MEDIA_FILE || !MOY_MEDIA_CLIENT)) {
        m->eof = m->fed_end;
        if (m->fed_end) {
            m->err = m->fed_err;
        }
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

// -- the file: the frame's job, on the VM's thread --------------------------------

// The job's file closed. Outside the job (a close from the lease table, the
// VM's end) it closes with no gate: the mount table it resolves through may
// be gone, and a file only read writes nothing as it closes.
static void file_close(media_t *m, int gated) {
    if (m->f != NULL) {
        if (gated) {
            vol_close(m->path, m->f);
        } else {
            moy_vol_close(m->f);
        }
        m->f = NULL;
    }
}

// The armed entry's file opened when the generation moved (or handed to the
// face where the page holds what the VM does not), and read into the stream
// buffer while it has room: a chunk at a time under the lock, so a command
// that moves the entry between two never sees a byte of the last one.
static void file_pump(media_t *m) {
    LOCK(m);
    uint32_t gen = m->armed;
    int ours = gen != 0 && m->face_gen != gen;
    uint32_t at = m->armed_at;
    int page = m->page;
    if (gen != m->f_gen && ours) {
        if (m->cur >= 0 && m->cur < m->n) {
            copy_text(m->pool + m->e_src[m->cur], m->path, sizeof(m->path));
        } else {
            ours = 0;
        }
    }
    UNLOCK(m);
    if (gen != m->f_gen) {
        file_close(m, 1);
        m->f_gen = gen;
        if (!ours) {
            return;
        }
        uint32_t size = 0;
        int rc = vol_open(m->path, 1, at, &m->f, &size);
        LOCK(m);
        if (m->armed != gen) {
            rc = -1;                        // moved on while it opened
        } else if (rc == 0) {
            m->e_size = size;
            m->f_left = size > at ? size - at : 0;
        } else if (page) {
            m->face_gen = gen;
        } else {
            m->fed_end = 1;
            m->fed_err = rc;
        }
        UNLOCK(m);
        if (rc != 0) {
            file_close(m, 1);
            WAKE(m);
            return;
        }
    }
    if (m->f == NULL) {
        return;
    }
    int moved = 0, end = 0;
    for (;;) {
        uint32_t room = in_room(m);
        if (room < MOY_MEDIA_READ_MIN && room < m->f_left) {
            break;
        }
        uint32_t want = room < m->f_left ? room : m->f_left;
        if (want > READ_CHUNK) {
            want = READ_CHUNK;
        }
        LOCK(m);
        if (m->armed != gen || m->fed_end) {
            UNLOCK(m);
            break;
        }
        uint32_t w = m->in_w, got = 0;
        int rc = 0;
        int g = moy_vol_gate_enter(m->path);
        while (rc == 0 && got < want) {
            uint32_t pos = (w + got) & IN_MASK, span = MOY_MEDIA_IN_BYTES - pos;
            if (span > want - got) {
                span = want - got;
            }
            size_t k = 0;
            rc = moy_vol_read(m->f, m->in + pos, span, &k);
            if (k == 0) {
                break;
            }
            got += (uint32_t)k;
        }
        moy_vol_gate_leave(g);
        m->f_left = got < m->f_left ? m->f_left - got : 0;
        end = rc != 0 || got == 0 || m->f_left == 0;
        STORE(&m->in_w, w + got);
        if (end) {
            m->fed_end = 1;
            m->fed_err = rc;
        }
        UNLOCK(m);
        moved = 1;
        if (end) {
            break;
        }
    }
    if (end) {
        file_close(m, 1);
    }
    if (moved) {
        WAKE(m);
    }
}

// -- the media keys -------------------------------------------------------------------

static void keys_open(media_t *m);
static void keys_close(media_t *m);

void moy_media_keys_on(struct moy_input *t) {
    LIFE_LOCK();
    media_t *m = s_m;
    if (m != NULL) {
        keys_close(m);
    }
    s_keys_t = t;
    if (m != NULL) {
        keys_open(m);
    }
    LIFE_UNLOCK();
}

int moy_media_key(uint16_t usage) {
    media_t *m = s_m;
    if (m == NULL || m->stop) {
        return 0;
    }
    moy_media_status_t st;
    moy_media_status(&st);
    switch (usage) {
        case MOY_MEDIA_KEY_PLAY_PAUSE:
            return moy_media_pause(!st.paused) == 0;
        case MOY_MEDIA_KEY_PLAY:
            return moy_media_pause(0) == 0;
        case MOY_MEDIA_KEY_PAUSE:
            return moy_media_pause(1) == 0;
        case MOY_MEDIA_KEY_NEXT:
        case MOY_MEDIA_KEY_PREV: {
            // While the list plays: past its end the service is ending.
            if (st.state != MOY_MEDIA_OPENING && st.state != MOY_MEDIA_PLAYING) {
                return 0;
            }
            int i = st.entry + (usage == MOY_MEDIA_KEY_NEXT ? 1 : -1);
            return i >= 0 && i < st.entries && moy_media_go(i) == 0;
        }
        case MOY_MEDIA_KEY_VOL_UP:
        case MOY_MEDIA_KEY_VOL_DOWN:
            return moy_media_volume(st.volume + (usage == MOY_MEDIA_KEY_VOL_UP
                                                     ? MOY_MEDIA_KEY_STEP
                                                     : -MOY_MEDIA_KEY_STEP)) == 0;
        default:
            return 0;
    }
}

static void keys_open(media_t *m) {
    m->keys_t = s_keys_t != NULL ? s_keys_t : moy_input_kernel();
    m->keys_r = -1;
    if (m->keys_t != NULL
        && moy_input_reader(m->keys_t, MOY_EV_BIT(MOY_EV_MEDIA), &m->keys_r) != MOY_INPUT_OK) {
        m->keys_r = -1;
    }
}

static void keys_close(media_t *m) {
    if (m->keys_r >= 0) {
        moy_input_reader_free(m->keys_t, m->keys_r);
        m->keys_r = -1;
    }
}

static void keys_answer(media_t *m) {
    if (m->keys_r < 0) {
        return;
    }
    moy_event_t ev[4];
    size_t n;
    while ((n = moy_input_events(m->keys_t, m->keys_r, ev, 4)) > 0) {
        for (size_t i = 0; i < n; i++) {
            if (ev[i].kind == MOY_EV_MEDIA && ev[i].b != 0u) {
                moy_media_key(ev[i].a);
            }
        }
    }
}

static bool end_check(media_t *m);

// Under the life gate: false when there is no service, or it ended now.
static bool pump_gated(void) {
    media_t *m = s_m;
    if (m == NULL) {
        return false;
    }
    keys_answer(m);
    file_pump(m);
#ifndef MOY_ON_BOARD
    for (int i = 0; i < 4 && moy_media_run(); i++) {
        file_pump(m);
    }
#endif
    return !end_check(m);
}

void moy_media_pump(void) {
    LIFE_LOCK();
    pump_gated();
    LIFE_UNLOCK();
}

// The service gone, the job answers false and the loop drops it on its own
// task: a teardown on another task never touches the loop's job table.
static bool media_job(void *arg) {
    (void)arg;
    LIFE_LOCK();
    bool live = pump_gated();
    LIFE_UNLOCK();
    return live;
}

// -- probe ---------------------------------------------------------------------------

// An MPEG audio frame header at `b`: its bytes a second, its rate and its
// samples a frame, or 0 for no header.
static int frame_head(const uint8_t *b, uint32_t *bps, uint32_t *hz, uint32_t *spf) {
    static const uint16_t K1[15] = { 0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320 };
    static const uint16_t K2[15] = { 0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160 };
    static const uint16_t R1[3] = { 44100, 48000, 32000 };
    static const uint16_t R2[3] = { 22050, 24000, 16000 };
    if (b[0] != 0xff || (b[1] & 0xe0) != 0xe0) {
        return 0;
    }
    int ver = (b[1] >> 3) & 3, layer = (b[1] >> 1) & 3, bi = b[2] >> 4, si = (b[2] >> 2) & 3;
    if (layer != 1 || bi == 0 || bi == 15 || si == 3 || ver == 1) {
        return 0;
    }
    *bps = (ver == 3 ? K1[bi] : K2[bi]) * 125u;
    *hz = ver == 3 ? R1[si] : R2[si] / (ver == 0 ? 2u : 1u);
    *spf = ver == 3 ? 1152u : 576u;
    return 1;
}

static int find4(const uint8_t *b, size_t n, size_t from, const char *pat) {
    for (size_t i = from; i + 4 <= n; i++) {
        if (memcmp(b + i, pat, 4) == 0) {
            return (int)i;
        }
    }
    return -1;
}

int moy_media_probe(const char *path, moy_media_probe_t *out) {
    memset(out, 0, sizeof(*out));
    uint8_t *b = moy_store_alloc(MOY_MEDIA_TAG_READ);
    if (b == NULL) {
        return -MOY_MEDIA_ENOMEM;
    }
    size_t n = 0;
    uint32_t size = 0, skip = 0;
    int rc = vol_read_at(path, 0, b, MOY_MEDIA_TAG_READ, &n, &size);
    if (rc == 0) {
        skip = moy_media_id3(b, n, out->title, out->artist, sizeof(out->title));
        if (skip > 0) {
            rc = vol_read_at(path, skip, b, MOY_MEDIA_TAG_READ, &n, &size);
        }
    }
    for (size_t at = 0; rc == 0 && at + 4 < n; at++) {
        uint32_t bps, hz, spf;
        if (!frame_head(b + at, &bps, &hz, &spf)) {
            continue;
        }
        int k = find4(b, n, at, "Xing");
        if (k < 0) {
            k = find4(b, n, at, "Info");
        }
        if (k >= 0 && (size_t)k < at + 64 && (size_t)k + 12 <= n && (b[k + 7] & 1)) {
            out->seconds = (uint32_t)((uint64_t)be32(b + k + 8) * spf / hz);
        } else if (bps > 0) {
            out->seconds = (size > skip ? size - skip : 0) / bps;
        }
        break;
    }
    moy_store_free(b, MOY_MEDIA_TAG_READ);
    return -rc;
}

#ifdef MOY_ON_BOARD
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

static int media_open(uint32_t pid, int rate, int channels);

// A board's rate and channels are its output's; elsewhere a run opened at 0
// takes the last run's (the host's mix's, which its face opens with).
static int s_rate = 22050, s_channels = 1;

int moy_media_open(int rate, int channels) {
    moy_lease_lock();                   // a client on another task opens with it held
    int rc = 0;
    if (s_m == NULL) {
        uint32_t pid = moy_log_spawn("media");
        int lr = moy_lease_hold(pid, MOY_LEASE_MEDIA);
        if (lr != MOY_LEASE_OK) {
            rc = lr == MOY_LEASE_BUSY ? -MOY_MEDIA_EBUSY : -MOY_MEDIA_ENOMEM;
        } else if ((rc = media_open(pid, rate, channels)) != 0) {
            moy_lease_let(pid, MOY_LEASE_MEDIA);
        } else {
            moy_log_say(MOY_LOG_INFO, pid, "start");
        }
    }
    moy_lease_unlock();
    return rc;
}

static int media_open(uint32_t pid, int rate, int channels) {
#if MOY_MEDIA_MIX
    moy_aud_out_start();
    rate = moy_aud_out_rate();
    channels = moy_aud_out_channels();
#else
    if (rate == 0) {
        rate = s_rate;
        channels = s_channels;
    }
#endif
    if (!moy_media_present()) {
        return -MOY_MEDIA_ENODEV;
    }
    if (rate < 8000 || rate > 96000 || channels < 1 || channels > 2) {
        return -MOY_MEDIA_EINVAL;
    }
    s_rate = rate;
    s_channels = channels;
    uint32_t share = share_of(channels);
    if (moy_share_take(MOY_SHARE_MEDIA, share) != MOY_PLAY_OK) {
        return -MOY_MEDIA_ENOSPC;
    }
    media_t *m = block_alloc(sizeof(media_t) + MOY_MEDIA_PCM_FRAMES * (size_t)channels * sizeof(int16_t));
    if (m == NULL) {
        moy_share_give(MOY_SHARE_MEDIA, share);
        return -MOY_MEDIA_ENOMEM;
    }
    m->pid = pid;
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
    m->seek_req = NO_SEEK;
    m->keys_r = -1;
    moy_mutex_init(&m->mtx, false);
#ifdef MOY_ON_BOARD
    m->stack = sram_take(m, MOY_MEDIA_STACK);
    if (m->stack == NULL) {
        m->stack = moy_kalloc(MOY_MEDIA_STACK, MOY_KMEM_PSRAM);
    }
#endif
    m->dec = sram_take(m, sizeof(mp3dec_t));
    if (m->dec == NULL) {
        m->dec = block_alloc(sizeof(mp3dec_t));
    }
    if (m->dec == NULL) {
#ifdef MOY_ON_BOARD
        moy_kfree(m->stack);
#endif
        block_free(m);
        moy_share_give(MOY_SHARE_MEDIA, share);
        return -MOY_MEDIA_ENOMEM;
    }
    mp3dec_init(m->dec);
    if (moy_loop_job == NULL || !moy_loop_job(media_job, NULL)) {
#ifdef MOY_ON_BOARD
        moy_kfree(m->stack);
#endif
        block_free(m->dec);
        block_free(m);
        moy_share_give(MOY_SHARE_MEDIA, share);
        return moy_loop_job == NULL ? -MOY_MEDIA_ENODEV : -MOY_MEDIA_EBUSY;
    }
#ifdef MOY_ON_BOARD
    m->tcb = moy_kalloc(sizeof(StaticTask_t), MOY_KMEM_INTERNAL | MOY_KMEM_ZERO);
    if (m->tcb != NULL && m->stack != NULL) {
        m->task = moy_task_spawn_static(MOY_TASK_MEDIA, task, m, m->stack, m->tcb);
    }
    if (m->task == NULL) {
        if (moy_loop_job_drop != NULL) {
            moy_loop_job_drop(media_job, NULL);
        }
        moy_kfree(m->tcb);
        moy_kfree(m->stack);
        block_free(m->dec);
        block_free(m);
        moy_share_give(MOY_SHARE_MEDIA, share);
        return -MOY_MEDIA_ENOMEM;
    }
#endif
    // the row counts PSRAM: what internal SRAM holds goes back to it
    moy_share_give(MOY_SHARE_MEDIA, m->sram);
    m->share -= m->sram;
#if MOY_MEDIA_MIX
    moy_aud_lock();
    s_m = m;
    moy_aud_unlock();
    moy_aud_bg(moy_media_mix);
#else
    s_m = m;
#endif
    keys_open(m);
    return 0;
}

#ifdef MOY_ON_BOARD
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

// The run unlinked, when it is still idle: no command reaches it after. Under
// the lease table's lock, which a client on another task holds for its whole
// call (moy_dev.h's handles), so a go() it made since the run was judged
// done keeps the run: false.
static bool unlink_run(media_t *m) {
    moy_lease_lock();
    bool idle = m->idle;
    if (idle) {
#if MOY_MEDIA_MIX
        moy_aud_bg(NULL);
        moy_aud_lock();
        s_m = NULL;
        moy_aud_unlock();
#else
        s_m = NULL;
#endif
    }
    moy_lease_unlock();
    return idle;
}

// Everything the unlinked run held given back: the task, the buffers, the
// MEDIA row, the lease and the radio's "media" row. The station itself is
// powered down by the console at its next release (moy_lease.h, THE RADIO).
static void reap(media_t *m) {
#ifdef MOY_ON_BOARD
    task_end(m);                        // idle: it waits on its notification and holds nothing
#endif
    moy_mutex_destroy(&m->mtx);
    uint32_t share = m->share, pid = m->pid;
    s_end.runs++;
    s_end.pid = pid;
    s_end.client = m->client;
    s_end.state = m->ending ? m->end_state : MOY_MEDIA_IDLE;
    s_end.err = m->ending ? m->end_err : 0;
    keys_close(m);
    conn_close(m);
    file_close(m, 0);
    block_free(m->dec);
    block_free(m);
    moy_share_give(MOY_SHARE_MEDIA, share);
    moy_lease_let(pid, MOY_LEASE_MEDIA);    // STALE where the row's drop ended the run
    uint32_t mask;
    moy_lease_wifi_release(pid, "media", 5, &mask);
    moy_log_say(MOY_LOG_INFO, pid, "exit");
}

static int close_gated(void) {
    media_t *m = s_m;
    if (m == NULL) {
        return -MOY_MEDIA_EBADF;
    }
    if (!m->stop) {
        moy_media_stop();
    }
    if (!m->idle || !unlink_run(m)) {
        return -MOY_MEDIA_EAGAIN;
    }
    reap(m);
    return 0;
}

int moy_media_close(void) {
    LIFE_LOCK();
    int rc = close_gated();
    LIFE_UNLOCK();
    return rc;
}

// The lease's row dropped from outside (moy_lease_drop, which holds the lease
// table's lock throughout): the run stops, and the frame's job ends it once
// its task lets go. The life gate is not taken here: the job takes it before
// the lease table's lock, so taking them the other way round could deadlock;
// the lease table's lock alone keeps the run linked (unlink_run).
void moy_media_lease_end(uint32_t owner) {
    moy_lease_lock();
    media_t *m = s_m;
    if (m != NULL && m->pid == owner && !m->stop) {
        moy_media_stop();
    }
    moy_lease_unlock();
}

// Whether the list is over: the task caught up with the last command and
// found ENDED, FAILED or nothing to play; its state and error into *st, *err.
static bool over(media_t *m, int *st, int *err) {
    LOCK(m);
    *st = m->state;
    *err = m->err;
    bool o = m->gen == m->seen
             && (*st == MOY_MEDIA_ENDED || *st == MOY_MEDIA_FAILED
                 || (*st == MOY_MEDIA_IDLE && m->cur < 0));
    UNLOCK(m);
    return o;
}

// The run's end, the frame's job's (under the life gate): a stop the task has
// let go of, or a list that is over -- ENDED, FAILED, or nothing to play, the
// task caught up with the last command -- once its tail has played out: the
// pull emptied the ring, or took nothing since the last frame (paused, or no
// output pulling). True when it ended and gave everything back.
static bool end_check(media_t *m) {
    if (!m->stop) {
        int st, err;
        if (!over(m, &st, &err)) {
            m->ending = 0;
            return false;
        }
        uint32_t pulled = m->pulled;
        bool drained = LOAD(&m->out_r) == LOAD(&m->out_w)
                       || (m->ending && pulled == m->end_pulled);
        if (!m->ending) {
            m->ending = 1;
            m->end_state = st;
            m->end_err = err;
        }
        m->end_pulled = pulled;
        if (!drained) {
            return false;
        }
        // Judged again with a client's command shut out (unlink_run's lock):
        // one made since moved it on.
        moy_lease_lock();
        bool still = over(m, &st, &err);
        if (still) {
            moy_media_stop();
        }
        moy_lease_unlock();
        if (!still) {
            m->ending = 0;
            return false;
        }
    }
#ifndef MOY_ON_BOARD
    if (!m->idle) {
        moy_media_run();
    }
#endif
    if (!m->idle || !unlink_run(m)) {
        return false;
    }
    reap(m);
    return true;
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

// moy_ssh: the server around Dropbear's session (moy_ssh.h), and the POSIX
// names Dropbear asks for (port/moy_ssh_port.h). Shared by the board and the
// host harness; what differs is behind ESP_PLATFORM.

#include "includes.h"
#include "dbutil.h"
#include "session.h"
#include "runopts.h"
#include "dbrandom.h"
#include "signkey.h"
#include "gensignkey.h"
#include "gened25519.h"
#include "dbmalloc.h"

#include "moy_ssh.h"
#include "moy_ssh_face.h"

#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#include "esp_random.h"
#include "esp_timer.h"
static uint32_t now_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000);
}
#else
#include <sys/random.h>
static uint32_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint32_t)(t.tv_sec * 1000 + t.tv_nsec / 1000000);
}
#endif

// The server's own state, PSRAM (moy_ssh_globals): the jump an exit takes,
// the stored hash and what getpwnam and crypt answer with.
typedef struct {
    jmp_buf jmp;
    char hash[MOY_SSH_HASH_MAX];
    char crypt[MOY_SSH_HASH_MAX];
    char user[33];
    struct passwd pw;
    moy_ssh_stats_t stats;
    size_t session_base;
    uint32_t accepted_at;
} moy_state_t;

static moy_state_t *g;
#define s_jmp (g->jmp)
#define s_hash (g->hash)
#define s_stats (g->stats)
#define s_session_base (g->session_base)
#define s_accepted_at (g->accepted_at)

// -- memory ------------------------------------------------------------------

// Every block carries its size ahead of it, so the counters follow frees.
typedef union {
    size_t n;
    max_align_t align;
} moy_head_t;

static void *raw_alloc(size_t n) {
#ifdef ESP_PLATFORM
    return heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#else
    return calloc(1, n);
#endif
}

static void raw_free(void *p) {
#ifdef ESP_PLATFORM
    heap_caps_free(p);
#else
    free(p);
#endif
}

static void count(size_t add, size_t sub) {
    if (g == NULL) {
        return;
    }
    s_stats.heap_now += add;
    s_stats.heap_now -= sub;
    if (s_stats.heap_now > s_stats.heap_peak) {
        s_stats.heap_peak = s_stats.heap_now;
    }
    if (s_stats.heap_now - s_session_base > s_stats.session_peak
            && s_stats.heap_now > s_session_base) {
        s_stats.session_peak = s_stats.heap_now - s_session_base;
    }
}

void *moy_ssh_calloc(size_t n, size_t size) {
    if (size != 0 && n > SIZE_MAX / size) {
        return NULL;
    }
    size_t want = n * size;
    moy_head_t *h = raw_alloc(sizeof *h + want);
    if (h == NULL) {
        return NULL;
    }
    h->n = want;
    count(want, 0);
    return h + 1;
}

void moy_ssh_free(void *p) {
    if (p == NULL) {
        return;
    }
    moy_head_t *h = (moy_head_t *)p - 1;
    count(0, h->n);
    raw_free(h);
}

void *moy_ssh_realloc(void *p, size_t n) {
    if (p == NULL) {
        return moy_ssh_calloc(1, n);
    }
    moy_head_t *h = (moy_head_t *)p - 1;
    void *q = moy_ssh_calloc(1, n);
    if (q == NULL) {
        return NULL;
    }
    memcpy(q, p, h->n < n ? h->n : n);
    moy_ssh_free(p);
    return q;
}

// -- the globals in PSRAM (port/moy_ssh_port.h) ----------------------------------

int moy_ssh_globals(void) {
    if (g != NULL) {
        return 0;
    }
    g = raw_alloc(sizeof *g);
    if (g == NULL) {
        return -1;
    }
    s_stats.heap_now = s_stats.heap_peak = sizeof *g;
    moy_ssh_g_ses = moy_ssh_calloc(1, sizeof(struct sshsession));
    moy_ssh_g_svr_ses = moy_ssh_calloc(1, sizeof(struct serversession));
    moy_ssh_g_opts = moy_ssh_calloc(1, sizeof(runopts));
    moy_ssh_g_svr_opts = moy_ssh_calloc(1, sizeof(svr_runopts));
    moy_ssh_g_ciphers = moy_ssh_calloc(TAB_SIZE, sizeof(struct ltc_cipher_descriptor));
    moy_ssh_g_hashes = moy_ssh_calloc(TAB_SIZE, sizeof(struct ltc_hash_descriptor));
    return g && moy_ssh_g_ses && moy_ssh_g_svr_ses && moy_ssh_g_opts && moy_ssh_g_svr_opts
           && moy_ssh_g_ciphers && moy_ssh_g_hashes ? 0 : -1;
}

// -- the POSIX names ----------------------------------------------------------

struct passwd *moy_ssh_getpwnam(const char *name) {
    if (g == NULL || s_hash[0] == '\0' || name == NULL || strlen(name) >= sizeof g->user) {
        return NULL;
    }
    struct passwd *pw = &g->pw;
    strcpy(g->user, name);
    memset(pw, 0, sizeof *pw);
    pw->pw_name = g->user;
    pw->pw_passwd = s_hash;
    pw->pw_dir = (char *)"/";
    pw->pw_shell = (char *)"/bin/sh";
    pw->pw_uid = geteuid();             // the server's own: one account
    pw->pw_gid = getegid();
    return pw;
}

int moy_ssh_getgroups(int n, gid_t *list) {
    (void)n;
    (void)list;
    errno = ENOSYS;
    return -1;
}

#ifdef ESP_PLATFORM
// The one account is the server's own (uid 0); newlib declares these and the
// port has none.
uid_t getuid(void) {
    return 0;
}

uid_t geteuid(void) {
    return 0;
}

gid_t getegid(void) {
    return 0;
}

struct passwd *getpwuid(uid_t uid) {
    (void)uid;
    return moy_ssh_getpwnam("moy");
}

char *dirname(char *path) {
    char *slash = path != NULL ? strrchr(path, '/') : NULL;
    if (slash == NULL) {
        return (char *)".";
    }
    if (slash == path) {
        return (char *)"/";
    }
    *slash = '\0';
    return path;
}
#endif

void moy_ssh_entropy(void *buf, size_t n) {
#ifdef ESP_PLATFORM
    esp_fill_random(buf, n);
#else
    if (getrandom(buf, n, 0) != (ssize_t)n) {
        dropbear_exit("no entropy");
    }
#endif
}

void moy_ssh_log(int priority, const char *msg) {
    (void)priority;
    printf("SSH %s\n", msg);
}

void moy_ssh_exit_jump(int code) {
    (void)code;
    longjmp(s_jmp, 1);
}

// -- the password ---------------------------------------------------------------

static void hex(const unsigned char *p, size_t n, char *out) {
    static const char d[] = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) {
        out[2 * i] = d[p[i] >> 4];
        out[2 * i + 1] = d[p[i] & 15];
    }
    out[2 * n] = '\0';
}

int moy_ssh_hash(const char *password, const char *salt, char *out, size_t cap) {
    size_t sl = strlen(salt);
    if (sl == 0 || sl > 32 || strspn(salt, "0123456789abcdef") != sl
            || cap < 6 + sl + 1 + 64 + 1) {
        return -1;
    }
    unsigned char d[32];
    hash_state hs;
    sha256_init(&hs);
    sha256_process(&hs, (const unsigned char *)salt, sl);
    sha256_process(&hs, (const unsigned char *)password, strlen(password));
    sha256_done(&hs, d);
    for (int i = 1; i < MOY_SSH_HASH_ROUNDS; i++) {
        sha256_init(&hs);
        sha256_process(&hs, d, sizeof d);
        sha256_process(&hs, (const unsigned char *)password, strlen(password));
        sha256_done(&hs, d);
    }
    char h[65];
    hex(d, sizeof d, h);
    m_burn(d, sizeof d);
    snprintf(out, cap, "$moy1$%s$%s", salt, h);
    return 0;
}

// Dropbear compares crypt(password, stored) with stored.
char *moy_ssh_crypt(const char *key, const char *stored) {
    char *out = g->crypt;
    char salt[33];
    const char *p = stored;
    if (strncmp(p, "$moy1$", 6) != 0) {
        return NULL;
    }
    p += 6;
    const char *e = strchr(p, '$');
    if (e == NULL || e - p > 32) {
        return NULL;
    }
    memcpy(salt, p, e - p);
    salt[e - p] = '\0';
    if (moy_ssh_hash(key, salt, out, MOY_SSH_HASH_MAX) != 0) {
        return NULL;
    }
    if (strcmp(out, stored) != 0) {
        s_stats.refused++;
    } else {
        s_stats.signed_in++;
    }
    return out;
}

// -- the server -------------------------------------------------------------------

int moy_ssh_setup(const uint8_t *key, size_t n, const char *hash, uint8_t **made,
                  size_t *made_n) {
    if (strlen(hash) >= MOY_SSH_HASH_MAX || strncmp(hash, "$moy1$", 6) != 0) {
        printf("SSH no password set\n");
        return -1;
    }
    if (moy_ssh_globals() != 0) {
        printf("SSH no memory\n");
        return -1;
    }
    strcpy(s_hash, hash);
    _dropbear_exit = svr_dropbear_exit;
    _dropbear_log = svr_dropbear_log;
    if (made != NULL) {
        *made = NULL;
        *made_n = 0;
    }

    static int once;
    if (once) {
        return 0;
    }
    // Setup's own allocations (the options, the host key) live in epoch 0,
    // which no session frees. An exit here returns to this frame.
    m_malloc_set_epoch(0);
    if (setjmp(s_jmp) != 0) {
        printf("SSH setup failed\n");
        return -1;
    }
    seedrandom();
    // -R: no key file is read or written here; the key is the caller's
    char *argv[] = {"dropbear", "-F", "-R", NULL};
    svr_getopts(3, argv);
    load_all_hostkeys();
    enum signkey_type type = DROPBEAR_SIGNKEY_ED25519;
    buffer *b;
    if (key != NULL) {
        b = buf_new(n);
        buf_putbytes(b, key, n);
        buf_setpos(b, 0);
        if (buf_get_priv_key(b, svr_opts.hostkey, &type) != DROPBEAR_SUCCESS
                || type != DROPBEAR_SIGNKEY_ED25519) {
            buf_burn_free(b);
            printf("SSH the host key does not read\n");
            return -1;
        }
        buf_burn_free(b);
    } else {
        uint32_t t0 = now_ms();
        svr_opts.hostkey->ed25519key = gen_ed25519_priv_key(256);
        b = buf_new(MAX_PRIVKEY_SIZE);
        buf_put_priv_key(b, svr_opts.hostkey, type);
        if (made != NULL) {
            *made = moy_ssh_calloc(1, b->len);
            if (*made != NULL) {
                memcpy(*made, b->data, b->len);
                *made_n = b->len;
            }
        }
        buf_burn_free(b);
        printf("SSH host key made in %u ms\n", (unsigned)(now_ms() - t0));
    }
    once = 1;
    return 0;
}

int moy_ssh_listen(int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in a;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(fd, (struct sockaddr *)&a, sizeof a) != 0 || listen(fd, 1) != 0) {
        close(fd);
        return -1;
    }
    s_stats.listening = 1;
    return fd;
}

int moy_ssh_pair(int fds[2]) {
#ifdef ESP_PLATFORM
    // lwIP has no socketpair: a listener on the loopback, connected once.
    int l = socket(AF_INET, SOCK_STREAM, 0);
    if (l < 0) {
        return -1;
    }
    struct sockaddr_in a;
    socklen_t al = sizeof a;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    a.sin_port = 0;
    int c = -1, s = -1;
    if (bind(l, (struct sockaddr *)&a, sizeof a) == 0 && listen(l, 1) == 0
            && getsockname(l, (struct sockaddr *)&a, &al) == 0) {
        c = socket(AF_INET, SOCK_STREAM, 0);
        if (c >= 0 && connect(c, (struct sockaddr *)&a, sizeof a) == 0) {
            s = accept(l, NULL, NULL);
        }
    }
    close(l);
    if (s < 0) {
        if (c >= 0) {
            close(c);
        }
        return -1;
    }
    int one = 1;
    setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    setsockopt(s, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    fds[0] = c;
    fds[1] = s;
    return 0;
#else
    return socketpair(AF_UNIX, SOCK_STREAM, 0, fds);
#endif
}

uint32_t moy_ssh_mark_shell(void) {
    s_stats.handshake_ms = now_ms() - s_accepted_at;
    return s_stats.handshake_ms;
}

int moy_ssh_serve_one(int lfd, int wait_ms) {
    fd_set r;
    FD_ZERO(&r);
    FD_SET(lfd, &r);
    struct timeval tv = {wait_ms / 1000, (wait_ms % 1000) * 1000};
    int n = select(lfd + 1, &r, NULL, NULL, &tv);
    if (n < 0) {
        return errno == EINTR ? 1 : -1;
    }
    if (n == 0) {
        return 1;
    }
    int fd = accept(lfd, NULL, NULL);
    if (fd < 0) {
        return 1;
    }
    s_stats.sessions++;
    s_accepted_at = now_ms();
    s_session_base = s_stats.heap_now;
    s_stats.session_peak = 0;

    memset(&ses, 0, sizeof ses);
    memset(&svr_ses, 0, sizeof svr_ses);
    m_malloc_set_epoch(1);
    if (setjmp(s_jmp) == 0) {
        seedrandom();
        svr_session(fd, -1);
    }
    m_malloc_free_epoch(1, 1);
    m_malloc_set_epoch(0);
    close(fd);
    moy_ssh_face_reap_all(5000);
    return 0;
}

void moy_ssh_stats(moy_ssh_stats_t *out) {
    if (g == NULL) {
        memset(out, 0, sizeof *out);
    } else {
        *out = s_stats;
    }
}

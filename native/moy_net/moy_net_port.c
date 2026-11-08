// The links' platform (moy_net.h): the clock, sleep and memory under them,
// and the HTTP listener's sockets -- lwip on a board, POSIX on the host, the
// unix port and the browser's build.
//
// Everything here depends on the platform, so a compile that is neither (the
// esp32 port's second compile of a usermod, which does not see ESP_PLATFORM)
// defines nothing, and the link takes the board's from the component's copy.
// The pure halves (moy_http.c, moy_webhost.c) call these and never test a
// platform themselves.

#if defined(__unix__) || defined(__APPLE__) || defined(__EMSCRIPTEN__)
#define MOY_NET_POSIX 1
#ifndef _DEFAULT_SOURCE
#define _DEFAULT_SOURCE             // clock_gettime and nanosleep under -std=c99
#endif
#else
#define MOY_NET_POSIX 0
#endif

#include <stdio.h>
#include <string.h>

#include "moy_net.h"
#include "moy_ota.h"

// -- the platform -----------------------------------------------------------------

#if defined(ESP_PLATFORM)
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

uint64_t moy_net_ms64(void) {
    return (uint64_t)(esp_timer_get_time() / 1000);
}

void moy_net_sleep_ms(uint32_t ms) {
    vTaskDelay(pdMS_TO_TICKS(ms ? ms : 1));
}

// The kernel's small-block pool (moy_kernel.h), where the image has it: the
// decoded keys live for the board's life, made at the first check.
void *moy_kpool_alloc(size_t n) __attribute__((weak));
bool moy_kpool_free(void *p) __attribute__((weak));

void *moy_net_alloc(size_t n) {
    void *q = moy_kpool_alloc != NULL ? moy_kpool_alloc(n) : NULL;
    if (q != NULL) {
        return q;
    }
    void *p = heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM);
    return p != NULL ? p : heap_caps_calloc(1, n, MALLOC_CAP_8BIT);
}

void moy_net_free(void *p) {
    if (moy_kpool_free != NULL && moy_kpool_free(p)) {
        return;
    }
    heap_caps_free(p);
}

#include "mbedtls/sha256.h"

void moy_net_sha256(const void *p, size_t n, uint8_t out[32]) {
    mbedtls_sha256(p, n, out, 0);
}
#elif MOY_NET_POSIX
#include <stdlib.h>
#include <time.h>

// What the host's tests move the clock by, so a window can run out in one
// (modulo 2^64: a skew back is its two's complement).
uint64_t moy_net_ms_skew;

uint64_t moy_net_ms64(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000u + (uint64_t)t.tv_nsec / 1000000u + moy_net_ms_skew;
}

void moy_net_sleep_ms(uint32_t ms) {
    struct timespec t = {(time_t)(ms / 1000u), (long)(ms % 1000u) * 1000000L};
    nanosleep(&t, NULL);
}

void *moy_net_alloc(size_t n) {
    return calloc(1, n ? n : 1);
}

void moy_net_free(void *p) {
    free(p);
}

void moy_net_sha256(const void *p, size_t n, uint8_t out[32]) {
    moy_sha256_t h;
    moy_sha256_init(&h);
    moy_sha256_update(&h, p, n);
    moy_sha256_final(&h, out);
}

// The machine's own clock, which its system keeps.
int moy_clock_state(int64_t *at) {
    if (at != NULL) {
        *at = (int64_t)time(NULL);
    }
    return MOY_CLOCK_SYNCED;
}

int moy_clock_server(const char *name) {
    return name != NULL && strlen(name) > MOY_CLOCK_SERVER_MAX ? -1 : 0;
}
#endif

// -- the clock a certificate's dates are read against ---------------------------

#if defined(ESP_PLATFORM) || MOY_NET_POSIX
#include <stdlib.h>

uint32_t moy_net_ms(void) {
    return (uint32_t)moy_net_ms64();
}

int64_t moy_clock_cert_time(int64_t now, int64_t floor, int synced) {
    return synced || now >= floor ? now : floor;
}

int moy_clock_future_counts(int64_t now, int64_t floor, int synced) {
    return synced || now >= floor;
}

int64_t moy_clock_build_day(void) {
    static const char mon[] = "JanFebMarAprMayJunJulAugSepOctNovDec";
    const char *d = __DATE__;                   // "Mmm dd yyyy"
    int m = 0;
    while (m < 12 && strncmp(mon + 3 * m, d, 3) != 0) {
        m++;
    }
    int day = (d[4] == ' ' ? 0 : d[4] - '0') * 10 + (d[5] - '0');
    int y = atoi(d + 7);
    // Days from 1970-01-01 to y-(m+1)-day (the civil-from-days inverse).
    int yy = y - (m < 2), era = yy / 400, yoe = yy - era * 400;
    int doy = (153 * (m + (m < 2 ? 10 : -2)) + 2) / 5 + day - 1;
    long days = (long)era * 146097 + (long)yoe * 365 + yoe / 4 - yoe / 100 + doy - 719468;
    return (int64_t)days * 86400;
}
#endif

// -- the listener -----------------------------------------------------------------

#if defined(ESP_PLATFORM) || MOY_NET_POSIX
#include <errno.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <netinet/in.h>
#include <unistd.h>
#if defined(ESP_PLATFORM)
#include "lwip/sockets.h"
#else
#include <sys/select.h>
#endif

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

static void timeout(int fd, int opt, uint32_t ms) {
    struct timeval tv = {(long)(ms / 1000u), (long)(ms % 1000u) * 1000L};
    setsockopt(fd, SOL_SOCKET, opt, &tv, sizeof(tv));
}

int moy_http_listen(uint16_t port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return -errno;
    }
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(fd, (struct sockaddr *)&a, sizeof(a)) != 0
        || listen(fd, MOY_HTTP_BACKLOG) != 0) {
        int e = errno;
        close(fd);
        return -(e ? e : 1);
    }
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL, 0) | O_NONBLOCK);
    return fd;
}

int moy_http_accept(int lfd, uint32_t *peer) {
    struct sockaddr_in a;
    socklen_t an = sizeof(a);
    memset(&a, 0, sizeof(a));
    int fd = accept(lfd, (struct sockaddr *)&a, &an);
    if (fd < 0) {
        return -1;
    }
    *peer = a.sin_family == AF_INET ? a.sin_addr.s_addr : 0u;
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL, 0) & ~O_NONBLOCK);
    timeout(fd, SO_RCVTIMEO, MOY_HTTP_RECV_MS);
    timeout(fd, SO_SNDTIMEO, MOY_HTTP_SEND_MS);
    #ifdef SO_NOSIGPIPE
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));
    #endif
    return fd;
}

int moy_http_recv(int fd, char *buf, size_t cap, moy_http_req_t *r,
                  size_t *got) {
    size_t n = 0;
    int head = MOY_HTTP_PARTIAL;
    memset(r, 0, sizeof(*r));
    while (n < cap) {
        ssize_t k = recv(fd, buf + n, cap - n < 1460u ? cap - n : 1460u, 0);
        if (k <= 0) {
            break;                  // the peer closed, timed out or failed
        }
        n += (size_t)k;
        if (head != MOY_HTTP_OK) {
            head = moy_http_parse(buf, n, r);
            if (head == MOY_HTTP_BAD) {
                break;
            }
        }
        if (head == MOY_HTTP_OK && n - r->head_end >= r->clen) {
            break;
        }
    }
    *got = n;
    if (head != MOY_HTTP_OK) {
        head = moy_http_parse(buf, n, r);
    }
    return head == MOY_HTTP_OK ? MOY_HTTP_OK : MOY_HTTP_BAD;
}

int moy_http_send(int fd, const void *p, size_t n) {
    const char *c = p;
    while (n > 0) {
        ssize_t k = send(fd, c, n, MSG_NOSIGNAL);
        if (k <= 0) {
            return -1;
        }
        c += k;
        n -= (size_t)k;
    }
    return 0;
}

void moy_http_close(int fd) {
    if (fd >= 0) {
        close(fd);
    }
}

int moy_udp_listen(uint16_t port) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) {
        return -errno;
    }
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(fd, (struct sockaddr *)&a, sizeof(a)) != 0) {
        int e = errno;
        close(fd);
        return -(e ? e : 1);
    }
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL, 0) | O_NONBLOCK);
    return fd;
}

uint16_t moy_udp_port(int fd) {
    struct sockaddr_in a;
    socklen_t len = sizeof(a);
    if (getsockname(fd, (struct sockaddr *)&a, &len) != 0) {
        return 0;
    }
    return ntohs(a.sin_port);
}

int moy_udp_recv(int fd, void *buf, size_t cap, uint8_t from[MOY_UDP_ADDR]) {
    struct sockaddr_in a;
    socklen_t len = sizeof(a);
    ssize_t k = recvfrom(fd, buf, cap, 0, (struct sockaddr *)&a, &len);
    if (k <= 0) {
        return 0;
    }
    memcpy(from, &a, sizeof(a) < MOY_UDP_ADDR ? sizeof(a) : MOY_UDP_ADDR);
    return (int)k;
}

int moy_udp_send(int fd, const void *buf, size_t n, const uint8_t to[MOY_UDP_ADDR]) {
    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    memcpy(&a, to, sizeof(a) < MOY_UDP_ADDR ? sizeof(a) : MOY_UDP_ADDR);
    return sendto(fd, buf, n, 0, (struct sockaddr *)&a, sizeof(a)) == (ssize_t)n ? 0 : -1;
}
#endif

// -- the board's pins (the Zero: MOY_NET_GPIO_PINS in its mpconfigboard.h) ----------

#if defined(ESP_PLATFORM) && defined(MOY_NET_GPIO_PINS)
#include "driver/gpio.h"

static const uint8_t s_gpio_pins[] = MOY_NET_GPIO_PINS;
#define GPIO_N ((int)sizeof(s_gpio_pins))
static uint8_t s_gpio_mode[GPIO_N];     // 0 untouched, 1 input, 2 output

static int gpio_slot(int pin) {
    for (int i = 0; i < GPIO_N; i++) {
        if (s_gpio_pins[i] == pin) {
            return i;
        }
    }
    return -1;
}

int moy_gpio_pins(const uint8_t **pins) {
    *pins = s_gpio_pins;
    return GPIO_N;
}

int moy_gpio_drive(int pin, int level) {
    int i = gpio_slot(pin);
    if (i < 0) {
        return -1;
    }
    if (s_gpio_mode[i] != 2) {
        gpio_reset_pin((gpio_num_t)pin);
        // Input too, so a read of a driven pin answers its level without
        // reconfiguring it.
        if (gpio_set_direction((gpio_num_t)pin, GPIO_MODE_INPUT_OUTPUT) != ESP_OK) {
            return -1;
        }
        s_gpio_mode[i] = 2;
    }
    return gpio_set_level((gpio_num_t)pin, level ? 1 : 0) == ESP_OK ? 0 : -1;
}

void moy_gpio_reset(int pin) {
    int i = gpio_slot(pin);
    if (i >= 0) {
        s_gpio_mode[i] = 0;
    }
}

int moy_gpio_sense(int pin) {
    int i = gpio_slot(pin);
    if (i < 0) {
        return -1;
    }
    if (s_gpio_mode[i] == 0) {
        gpio_reset_pin((gpio_num_t)pin);
        gpio_set_direction((gpio_num_t)pin, GPIO_MODE_INPUT);
        gpio_set_pull_mode((gpio_num_t)pin, GPIO_PULLUP_ONLY);
        s_gpio_mode[i] = 1;
    }
    return gpio_get_level((gpio_num_t)pin) ? 1 : 0;
}
#endif

// -- the client's connection and the slot -------------------------------------------

#if defined(ESP_PLATFORM)
#include <netdb.h>
#include <stdbool.h>
#include <stdlib.h>
#include "esp_flash_partitions.h"
#include "esp_image_format.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_random.h"
#include "mbedtls/net_sockets.h"
#include "mbedtls/platform_time.h"
#include "mbedtls/ssl.h"
#if defined(MBEDTLS_SSL_PROTO_TLS1_3) || defined(MBEDTLS_USE_PSA_CRYPTO)
#include "psa/crypto.h"
#endif
#if defined(CONFIG_MBEDTLS_CERTIFICATE_BUNDLE)
#include "esp_crt_bundle.h"
// The bundle's own check, which esp_crt_bundle_attach installs and the
// image's verify callback below wraps.
extern int esp_crt_verify_callback(void *buf, mbedtls_x509_crt *crt, int depth,
                                   uint32_t *flags);
#endif

// The kernel's task watchdog, fed while the frame loop has it armed: a
// connect or a read that waits is the console waiting on purpose.
extern bool moy_kernel_watchdog(uint32_t *timeout_ms, uint32_t *max_gap_ms,
                                uint32_t *frames, bool reset) __attribute__((weak));
extern void moy_kernel_feed(void) __attribute__((weak));

static void tick(void) {
    static uint32_t last;
    uint32_t t, g, f, now = moy_net_ms();
    if (moy_kernel_feed != NULL && moy_kernel_watchdog != NULL && now - last >= 500u
        && moy_kernel_watchdog(&t, &g, &f, false)) {
        last = now;
        moy_kernel_feed();
    }
}

#define CONN_WAIT_MS 500            // one socket wait; a tick between two

typedef struct {
    int fd;
    int tls;
    mbedtls_ssl_context ssl;
    mbedtls_ssl_config conf;
} conn_t;

static int rng(void *ctx, unsigned char *out, size_t n) {
    (void)ctx;
    esp_fill_random(out, n);
    return 0;
}

int moy_conn_verifies(void) {
    #if defined(CONFIG_MBEDTLS_CERTIFICATE_BUNDLE)
    return 1;
    #else
    return 0;
    #endif
}

#if defined(CONFIG_MBEDTLS_CERTIFICATE_BUNDLE)
// A certificate's dates read the clock (moy_clock_cert_time): the real time
// once NTP has answered (moy_clock_state); before that a board's clock is the
// uptime, so it is read raised to the day this image was built. A certificate
// that expired before the build is refused on every board, one that expired
// after it once the clock is set past that, and while the clock is behind the
// build a not-yet-valid date is not held against a certificate (one issued
// after the build is the usual case for 90-day certificates).
extern int moy_clock_state(int64_t *at) __attribute__((weak));

#define CLOCK_WAIT_MS 3000          // a first answer a handshake waits for

static mbedtls_time_t (*s_clock)(mbedtls_time_t *);
static mbedtls_time_t s_floor;

static int synced(void) {
    return moy_clock_state != NULL && moy_clock_state(NULL) == MOY_CLOCK_SYNCED;
}

// The station is up for this handshake, so a request NTP has out is waited
// for, briefly: its answer is what makes the dates exact.
static void await_clock(void) {
    if (moy_clock_state == NULL) {
        return;
    }
    uint32_t t0 = moy_net_ms();
    while (moy_clock_state(NULL) == MOY_CLOCK_ASKING && moy_net_ms() - t0 < CLOCK_WAIT_MS) {
        moy_net_sleep_ms(50);
        tick();
    }
}

static mbedtls_time_t tls_now(mbedtls_time_t *t) {
    mbedtls_time_t n = s_clock != NULL ? s_clock(NULL) : 0;
    n = (mbedtls_time_t)moy_clock_cert_time(n, s_floor, synced());
    if (t != NULL) {
        *t = n;
    }
    return n;
}

static int verify(void *ctx, mbedtls_x509_crt *crt, int depth, uint32_t *flags) {
    mbedtls_time_t n = s_clock != NULL ? s_clock(NULL) : 0;
    if (!moy_clock_future_counts(n, s_floor, synced())) {
        *flags &= ~(uint32_t)MBEDTLS_X509_BADCERT_FUTURE;
    }
    return esp_crt_verify_callback(ctx, crt, depth, flags);
}

static int verify_setup(mbedtls_ssl_config *conf) {
    await_clock();
    if (mbedtls_time != tls_now) {
        s_floor = (mbedtls_time_t)moy_clock_build_day();
        s_clock = mbedtls_time;
        mbedtls_platform_set_time(tls_now);
    }
    if (esp_crt_bundle_attach(conf) != ESP_OK) {
        return -1;
    }
    mbedtls_ssl_conf_authmode(conf, MBEDTLS_SSL_VERIFY_REQUIRED);
    mbedtls_ssl_conf_verify(conf, verify, NULL);
    return 0;
}
#endif

static int bio_send(void *ctx, const unsigned char *b, size_t n) {
    int k = send(((conn_t *)ctx)->fd, b, n, 0);
    if (k < 0) {
        return errno == EAGAIN || errno == EWOULDBLOCK ? MBEDTLS_ERR_SSL_WANT_WRITE
               : MBEDTLS_ERR_NET_SEND_FAILED;
    }
    return k;
}

static int bio_recv(void *ctx, unsigned char *b, size_t n) {
    int k = recv(((conn_t *)ctx)->fd, b, n, 0);
    if (k < 0) {
        return errno == EAGAIN || errno == EWOULDBLOCK ? MBEDTLS_ERR_SSL_WANT_READ
               : MBEDTLS_ERR_NET_RECV_FAILED;
    }
    return k;
}

static void conn_free(conn_t *c) {
    if (c->tls) {
        mbedtls_ssl_free(&c->ssl);
        mbedtls_ssl_config_free(&c->conf);
    }
    if (c->fd >= 0) {
        close(c->fd);
    }
    moy_net_free(c);
}

int moy_conn_open(const char *host, uint16_t port, int tls, void **out) {
    *out = NULL;
    if (tls == MOY_CONN_TLS && !moy_conn_verifies()) {
        return -MOY_HTTPC_ETLS;
    }
    char ps[8];
    snprintf(ps, sizeof(ps), "%u", (unsigned)port);
    struct addrinfo hints, *ai = NULL;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    tick();
    if (getaddrinfo(host, ps, &hints, &ai) != 0 || ai == NULL) {
        return -EHOSTUNREACH;
    }
    conn_t *c = moy_net_alloc(sizeof(conn_t));
    if (c == NULL) {
        freeaddrinfo(ai);
        return -ENOMEM;
    }
    c->fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
    if (c->fd < 0) {
        freeaddrinfo(ai);
        moy_net_free(c);
        return -ENOMEM;
    }
    // The connect without blocking, waited out in ticks.
    fcntl(c->fd, F_SETFL, fcntl(c->fd, F_GETFL, 0) | O_NONBLOCK);
    int rc = connect(c->fd, ai->ai_addr, ai->ai_addrlen);
    freeaddrinfo(ai);
    if (rc != 0 && errno != EINPROGRESS) {
        rc = -errno;
        conn_free(c);
        return rc;
    }
    if (rc != 0) {
        uint32_t t0 = moy_net_ms();
        for (;;) {
            fd_set w;
            FD_ZERO(&w);
            FD_SET(c->fd, &w);
            struct timeval tv = {0, CONN_WAIT_MS * 1000};
            int k = select(c->fd + 1, NULL, &w, NULL, &tv);
            tick();
            if (k > 0) {
                int err = 0;
                socklen_t len = sizeof(err);
                getsockopt(c->fd, SOL_SOCKET, SO_ERROR, &err, &len);
                if (err != 0) {
                    conn_free(c);
                    return -err;
                }
                break;
            }
            if (k < 0 || moy_net_ms() - t0 >= MOY_HTTPC_IO_MS) {
                conn_free(c);
                return -ETIMEDOUT;
            }
        }
    }
    fcntl(c->fd, F_SETFL, fcntl(c->fd, F_GETFL, 0) & ~O_NONBLOCK);
    timeout(c->fd, SO_RCVTIMEO, CONN_WAIT_MS);
    timeout(c->fd, SO_SNDTIMEO, MOY_HTTPC_IO_MS);
    if (tls == MOY_CONN_PLAIN) {
        *out = c;
        return 0;
    }
    #if defined(MBEDTLS_SSL_PROTO_TLS1_3) || defined(MBEDTLS_USE_PSA_CRYPTO)
    psa_crypto_init();
    #endif
    c->tls = 1;
    mbedtls_ssl_init(&c->ssl);
    mbedtls_ssl_config_init(&c->conf);
    rc = mbedtls_ssl_config_defaults(&c->conf, MBEDTLS_SSL_IS_CLIENT,
                                     MBEDTLS_SSL_TRANSPORT_STREAM, MBEDTLS_SSL_PRESET_DEFAULT);
    if (rc == 0) {
        if (tls == MOY_CONN_TLS_SIGNED) {
            // The updater's: no certificate is verified, the manifest's
            // signature decides what installs (moy_ota.h).
            mbedtls_ssl_conf_authmode(&c->conf, MBEDTLS_SSL_VERIFY_NONE);
        }
        #if defined(CONFIG_MBEDTLS_CERTIFICATE_BUNDLE)
        else if (verify_setup(&c->conf) != 0) {
            rc = -1;
        }
        #endif
    }
    if (rc == 0) {
        mbedtls_ssl_conf_rng(&c->conf, rng, NULL);
        rc = mbedtls_ssl_setup(&c->ssl, &c->conf);
    }
    if (rc == 0) {
        rc = mbedtls_ssl_set_hostname(&c->ssl, host);
    }
    if (rc != 0) {
        conn_free(c);
        return -ENOMEM;
    }
    mbedtls_ssl_set_bio(&c->ssl, c, bio_send, bio_recv, NULL);
    uint32_t t0 = moy_net_ms();
    while ((rc = mbedtls_ssl_handshake(&c->ssl)) != 0) {
        tick();
        if ((rc != MBEDTLS_ERR_SSL_WANT_READ && rc != MBEDTLS_ERR_SSL_WANT_WRITE)
            || moy_net_ms() - t0 >= MOY_HTTPC_IO_MS) {
            conn_free(c);
            return rc == MBEDTLS_ERR_SSL_WANT_READ ? -ETIMEDOUT
                   : rc == MBEDTLS_ERR_X509_CERT_VERIFY_FAILED
                     || rc == MBEDTLS_ERR_X509_FATAL_ERROR ? -MOY_HTTPC_ECERT
                   : -ECONNABORTED;
        }
    }
    *out = c;
    return 0;
}

int moy_conn_read(void *conn, void *p, size_t n) {
    conn_t *c = conn;
    uint32_t t0 = moy_net_ms();
    for (;;) {
        int k;
        if (c->tls) {
            k = mbedtls_ssl_read(&c->ssl, p, n);
            if (k == MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY) {
                return 0;
            }
            if (k >= 0) {
                return k;
            }
            if (k != MBEDTLS_ERR_SSL_WANT_READ && k != MBEDTLS_ERR_SSL_WANT_WRITE
                #ifdef MBEDTLS_ERR_SSL_RECEIVED_NEW_SESSION_TICKET
                && k != MBEDTLS_ERR_SSL_RECEIVED_NEW_SESSION_TICKET
                #endif
                ) {
                return k == MBEDTLS_ERR_NET_RECV_FAILED ? -ECONNRESET : -EIO;
            }
        } else {
            k = recv(c->fd, p, n, 0);
            if (k >= 0) {
                return k;
            }
            if (errno != EAGAIN && errno != EWOULDBLOCK) {
                return -errno;
            }
        }
        tick();
        if (moy_net_ms() - t0 >= MOY_HTTPC_IO_MS) {
            return -ETIMEDOUT;
        }
    }
}

int moy_conn_read_wait(void *conn, void *p, size_t n, uint32_t ms) {
    conn_t *c = conn;
    if (c->tls && mbedtls_ssl_get_bytes_avail(&c->ssl) > 0) {
        return moy_conn_read(conn, p, n);
    }
    fd_set r;
    FD_ZERO(&r);
    FD_SET(c->fd, &r);
    struct timeval tv = {(long)(ms / 1000u), (long)(ms % 1000u) * 1000L};
    int k = select(c->fd + 1, &r, NULL, NULL, &tv);
    if (k == 0) {
        return -MOY_HTTPC_EAGAIN;
    }
    if (k < 0) {
        return -errno;
    }
    if (c->tls) {
        return moy_conn_read(conn, p, n);   // a record's bytes have begun to arrive
    }
    k = recv(c->fd, p, n, 0);
    if (k >= 0) {
        return k;
    }
    return errno == EAGAIN || errno == EWOULDBLOCK ? -MOY_HTTPC_EAGAIN : -errno;
}

int moy_conn_write(void *conn, const void *p, size_t n) {
    conn_t *c = conn;
    const unsigned char *b = p;
    uint32_t t0 = moy_net_ms();
    while (n > 0) {
        int k = c->tls ? mbedtls_ssl_write(&c->ssl, b, n) : send(c->fd, b, n, 0);
        if (k > 0) {
            b += k;
            n -= (size_t)k;
            continue;
        }
        if (c->tls && k != MBEDTLS_ERR_SSL_WANT_READ && k != MBEDTLS_ERR_SSL_WANT_WRITE) {
            return -EIO;
        }
        if (!c->tls && k < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
            return -errno;
        }
        tick();
        if (moy_net_ms() - t0 >= MOY_HTTPC_IO_MS) {
            return -ETIMEDOUT;
        }
    }
    return 0;
}

void moy_conn_close(void *conn) {
    if (conn != NULL) {
        conn_free(conn);
    }
}

// The inactive app slot, written as the copied-image path always has: each
// 4 KB sector erased as the write first reaches it, the image checked whole
// at close, and the bootloader pointed at it only by moy_slot_boot.
static const esp_partition_t *s_slot;
static uint32_t s_slot_off, s_slot_erased;
static uint8_t s_slot_closed;

int moy_slot_open(uint32_t size, uint32_t *cap) {
    s_slot = esp_ota_get_next_update_partition(NULL);
    s_slot_off = 0;
    s_slot_erased = 0;
    s_slot_closed = 0;
    if (s_slot == NULL) {
        *cap = 0;
        return -ENODEV;
    }
    *cap = s_slot->size;
    return size > s_slot->size ? -EFBIG : 0;
}

int moy_slot_write(const void *p, size_t n) {
    if (s_slot == NULL || s_slot_closed) {
        return -EBADF;
    }
    if (n > s_slot->size - s_slot_off) {
        return -ENOSPC;
    }
    uint32_t end = s_slot_off + (uint32_t)n;
    if (end > s_slot_erased) {
        uint32_t to = (end + 4095u) & ~4095u;          // whole 4 KB sectors
        if (esp_partition_erase_range(s_slot, s_slot_erased, to - s_slot_erased) != ESP_OK) {
            return -EIO;
        }
        s_slot_erased = to;
    }
    if (esp_partition_write(s_slot, s_slot_off, p, n) != ESP_OK) {
        return -EIO;
    }
    s_slot_off = end;
    tick();
    return 0;
}

int moy_slot_close(void) {
    if (s_slot == NULL) {
        return -EBADF;
    }
    esp_partition_pos_t pos = {.offset = s_slot->address, .size = s_slot->size};
    esp_image_metadata_t meta;
    if (esp_image_verify(ESP_IMAGE_VERIFY_SILENT, &pos, &meta) != ESP_OK) {
        s_slot = NULL;
        return -EINVAL;
    }
    s_slot_closed = 1;
    return 0;
}

int moy_slot_boot(char *label, size_t cap) {
    if (s_slot == NULL || !s_slot_closed) {
        return -EBADF;
    }
    esp_err_t e = esp_ota_set_boot_partition(s_slot);
    if (e != ESP_OK) {
        return -EIO;
    }
    snprintf(label, cap, "%s", s_slot->label);
    s_slot = NULL;
    return 0;
}

void moy_slot_abort(void) {
    s_slot = NULL;
    s_slot_closed = 0;
}

#elif MOY_NET_POSIX
#include <netdb.h>

// The host's connection: plain TCP; TLS is the boards'.
typedef struct {
    int fd;
} conn_t;

int moy_conn_verifies(void) {
    return 0;
}

int moy_conn_open(const char *host, uint16_t port, int tls, void **out) {
    *out = NULL;
    if (tls != MOY_CONN_PLAIN) {
        return -EPROTONOSUPPORT;
    }
    char ps[8];
    snprintf(ps, sizeof(ps), "%u", (unsigned)port);
    struct addrinfo hints, *ai = NULL;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host, ps, &hints, &ai) != 0 || ai == NULL) {
        return -EHOSTUNREACH;
    }
    conn_t *c = moy_net_alloc(sizeof(conn_t));
    c->fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
    int rc = c->fd < 0 ? -errno : connect(c->fd, ai->ai_addr, ai->ai_addrlen) != 0 ? -errno : 0;
    freeaddrinfo(ai);
    if (rc != 0) {
        if (c->fd >= 0) {
            close(c->fd);
        }
        moy_net_free(c);
        return rc;
    }
    timeout(c->fd, SO_RCVTIMEO, MOY_HTTPC_IO_MS);
    timeout(c->fd, SO_SNDTIMEO, MOY_HTTPC_IO_MS);
    *out = c;
    return 0;
}

int moy_conn_read(void *conn, void *p, size_t n) {
    ssize_t k = recv(((conn_t *)conn)->fd, p, n, 0);
    if (k < 0) {
        return errno == EAGAIN || errno == EWOULDBLOCK ? -ETIMEDOUT : -errno;
    }
    return (int)k;
}

int moy_conn_read_wait(void *conn, void *p, size_t n, uint32_t ms) {
    int fd = ((conn_t *)conn)->fd;
    fd_set r;
    FD_ZERO(&r);
    FD_SET(fd, &r);
    struct timeval tv = {(long)(ms / 1000u), (long)(ms % 1000u) * 1000L};
    int k = select(fd + 1, &r, NULL, NULL, &tv);
    if (k == 0) {
        return -MOY_HTTPC_EAGAIN;
    }
    if (k < 0) {
        return -errno;
    }
    return moy_conn_read(conn, p, n);
}

int moy_conn_write(void *conn, const void *p, size_t n) {
    return moy_http_send(((conn_t *)conn)->fd, p, n) == 0 ? 0 : -EPIPE;
}

void moy_conn_close(void *conn) {
    if (conn != NULL) {
        close(((conn_t *)conn)->fd);
        moy_net_free(conn);
    }
}

// The host's slot: memory the tests read back, `moy_slot_host_cap` bytes,
// checked at close as a board's is in its first byte alone. A write past
// `moy_slot_host_room` bytes fails ENOSPC as a flash that will not take them.
uint32_t moy_slot_host_cap = 4u << 20;
uint32_t moy_slot_host_room = 0xffffffffu;
uint8_t *moy_slot_host_data;
uint32_t moy_slot_host_n;
char moy_slot_host_booted[16];
static int s_open, s_closed;

int moy_slot_open(uint32_t size, uint32_t *cap) {
    *cap = moy_slot_host_cap;
    free(moy_slot_host_data);
    moy_slot_host_data = malloc(moy_slot_host_cap ? moy_slot_host_cap : 1);
    moy_slot_host_n = 0;
    s_open = 1;
    s_closed = 0;
    return size > moy_slot_host_cap ? -EFBIG : 0;
}

int moy_slot_write(const void *p, size_t n) {
    if (!s_open || s_closed) {
        return -EBADF;
    }
    if (n > moy_slot_host_cap - moy_slot_host_n
        || moy_slot_host_n + n > moy_slot_host_room) {
        return -ENOSPC;
    }
    memcpy(moy_slot_host_data + moy_slot_host_n, p, n);
    moy_slot_host_n += (uint32_t)n;
    return 0;
}

int moy_slot_close(void) {
    if (!s_open || moy_slot_host_n == 0 || moy_slot_host_data[0] != 0xE9) {
        s_open = 0;
        return -EINVAL;
    }
    s_closed = 1;
    return 0;
}

int moy_slot_boot(char *label, size_t cap) {
    if (!s_open || !s_closed) {
        return -EBADF;
    }
    snprintf(moy_slot_host_booted, sizeof(moy_slot_host_booted), "ota_1");
    snprintf(label, cap, "%s", moy_slot_host_booted);
    s_open = 0;
    return 0;
}

void moy_slot_abort(void) {
    s_open = 0;
    s_closed = 0;
}
#endif

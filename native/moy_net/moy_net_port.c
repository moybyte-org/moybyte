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

uint32_t moy_net_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000);
}

void moy_net_sleep_ms(uint32_t ms) {
    vTaskDelay(pdMS_TO_TICKS(ms ? ms : 1));
}

void *moy_net_alloc(size_t n) {
    void *p = heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM);
    return p != NULL ? p : heap_caps_calloc(1, n, MALLOC_CAP_8BIT);
}

void moy_net_free(void *p) {
    heap_caps_free(p);
}
#elif MOY_NET_POSIX
#include <stdlib.h>
#include <time.h>

// What the host's tests move the clock by, so a window can run out in one.
uint32_t moy_net_ms_skew;

uint32_t moy_net_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint32_t)((uint64_t)t.tv_sec * 1000u + (uint64_t)t.tv_nsec / 1000000u)
           + moy_net_ms_skew;
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

int moy_http_accept(int lfd) {
    int fd = accept(lfd, NULL, NULL);
    if (fd < 0) {
        return -1;
    }
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
#include "esp_flash_partitions.h"
#include "esp_image_format.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_random.h"
#include "mbedtls/net_sockets.h"
#include "mbedtls/ssl.h"
#if defined(MBEDTLS_SSL_PROTO_TLS1_3) || defined(MBEDTLS_USE_PSA_CRYPTO)
#include "psa/crypto.h"
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
    if (!tls) {
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
        // No certificate is verified: the manifest's signature decides (moy_ota.h).
        mbedtls_ssl_conf_authmode(&c->conf, MBEDTLS_SSL_VERIFY_NONE);
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
            return rc == MBEDTLS_ERR_SSL_WANT_READ ? -ETIMEDOUT : -ECONNABORTED;
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

int moy_conn_open(const char *host, uint16_t port, int tls, void **out) {
    *out = NULL;
    if (tls) {
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
// checked at close as a board's is in its first byte alone.
uint32_t moy_slot_host_cap = 4u << 20;
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
    if (n > moy_slot_host_cap - moy_slot_host_n) {
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

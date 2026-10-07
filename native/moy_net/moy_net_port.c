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
#endif

// Address text both ways and the lookup's messages, over the SDK's lwIP
// layout (sdk/sysroot/include). glibc's own serve the host.
#include "lc.h"

#if defined(LC_NEWLIB)

#include <arpa/inet.h>
#include <netdb.h>
#include <string.h>

LC_API int inet_aton(const char *s, struct in_addr *out) {
    uint32_t parts[4];
    int n = 0;
    for (;;) {
        int neg, over;
        char *end;
        if (*s < '0' || *s > '9') {
            return 0;
        }
        unsigned long long v = lc_parse_u(s, &end, 0, &neg, &over);
        if (end == s || over || v > 0xffffffffULL) {
            return 0;
        }
        parts[n++] = (uint32_t)v;
        s = end;
        if (*s != '.' || n == 4) {
            break;
        }
        s++;
    }
    if (*s != '\0') {
        return 0;
    }
    uint32_t a;
    switch (n) {
    case 1:
        a = parts[0];
        break;
    case 2:
        if (parts[0] > 255 || parts[1] > 0xffffff) {
            return 0;
        }
        a = parts[0] << 24 | parts[1];
        break;
    case 3:
        if (parts[0] > 255 || parts[1] > 255 || parts[2] > 0xffff) {
            return 0;
        }
        a = parts[0] << 24 | parts[1] << 16 | parts[2];
        break;
    default:
        for (int i = 0; i < 4; i++) {
            if (parts[i] > 255) {
                return 0;
            }
        }
        a = parts[0] << 24 | parts[1] << 16 | parts[2] << 8 | parts[3];
    }
    if (out != NULL) {
        out->s_addr = htonl(a);
    }
    return 1;
}

LC_API in_addr_t inet_addr(const char *s) {
    struct in_addr a;
    return inet_aton(s, &a) ? a.s_addr : INADDR_NONE;
}

LC_API const char *inet_ntop(int af, const void *src, char *dst, socklen_t size) {
    char b[INET6_ADDRSTRLEN];
    const uint8_t *p = src;
    if (af == AF_INET) {
        snprintf(b, sizeof b, "%u.%u.%u.%u", p[0], p[1], p[2], p[3]);
    } else if (af == AF_INET6) {
        // The longest run of two or more zero groups is "::".
        int best = -1, best_n = 0;
        for (int i = 0; i < 8;) {
            int k = i;
            while (k < 8 && p[2 * k] == 0 && p[2 * k + 1] == 0) {
                k++;
            }
            if (k - i > best_n && k - i >= 2) {
                best = i;
                best_n = k - i;
            }
            i = k > i ? k : i + 1;
        }
        int n = 0;
        for (int i = 0; i < 8; i++) {
            if (i == best) {
                n += snprintf(b + n, sizeof b - (size_t)n, i == 0 ? "::" : ":");
                i += best_n - 1;
                continue;
            }
            n += snprintf(b + n, sizeof b - (size_t)n, "%x%s", p[2 * i] << 8 | p[2 * i + 1],
                          i < 7 && i + 1 != best ? ":" : "");
        }
    } else {
        errno = EAFNOSUPPORT;
        return NULL;
    }
    if (strlen(b) + 1 > size) {
        errno = ENOSPC;
        return NULL;
    }
    strcpy(dst, b);
    return dst;
}

LC_API char *inet_ntoa(struct in_addr in) {
    static char b[INET_ADDRSTRLEN];
    return (char *)inet_ntop(AF_INET, &in, b, sizeof b);
}

static int hex(int c) {
    return c >= '0' && c <= '9' ? c - '0' : (c | 0x20) >= 'a' && (c | 0x20) <= 'f'
           ? (c | 0x20) - 'a' + 10 : -1;
}

LC_API int inet_pton(int af, const char *s, void *dst) {
    if (af == AF_INET) {
        uint8_t a[4];
        for (int i = 0; i < 4; i++) {
            int v = 0, k = 0;
            while (s[k] >= '0' && s[k] <= '9' && k < 3) {
                v = v * 10 + (s[k++] - '0');
            }
            if (k == 0 || v > 255 || (k > 1 && s[0] == '0')) {
                return 0;
            }
            a[i] = (uint8_t)v;
            s += k;
            if (i < 3 && *s++ != '.') {
                return 0;
            }
        }
        if (*s != '\0') {
            return 0;
        }
        memcpy(dst, a, 4);
        return 1;
    }
    if (af != AF_INET6) {
        errno = EAFNOSUPPORT;
        return -1;
    }
    uint16_t g[8] = { 0 };
    int n = 0, gap = -1;
    if (s[0] == ':' && s[1] == ':') {
        gap = 0;
        s += 2;
    }
    while (*s && n < 8) {
        int v = 0, k = 0, d;
        while ((d = hex(s[k])) >= 0 && k < 4) {
            v = v * 16 + d;
            k++;
        }
        if (k == 0) {
            return 0;
        }
        g[n++] = (uint16_t)v;
        s += k;
        if (*s == ':') {
            s++;
            if (*s == ':') {
                if (gap >= 0) {
                    return 0;
                }
                gap = n;
                s++;
            } else if (*s == '\0') {
                return 0;
            }
        } else if (*s != '\0') {
            return 0;
        }
    }
    if (*s != '\0' || (gap < 0 && n != 8)) {
        return 0;
    }
    uint8_t *out = dst;
    int tail = gap >= 0 ? n - gap : 0;
    memset(out, 0, 16);
    for (int i = 0; i < n - tail; i++) {
        out[2 * i] = (uint8_t)(g[i] >> 8);
        out[2 * i + 1] = (uint8_t)g[i];
    }
    for (int i = 0; i < tail; i++) {
        int at = 8 - tail + i;
        out[2 * at] = (uint8_t)(g[n - tail + i] >> 8);
        out[2 * at + 1] = (uint8_t)g[n - tail + i];
    }
    return 1;
}

LC_API const char *gai_strerror(int code) {
    switch (code) {
    case 0:
        return "Success";
    case EAI_NONAME:
        return "Name or service not known";
    case EAI_SERVICE:
        return "Service not supported";
    case EAI_FAIL:
        return "Non-recoverable failure in name resolution";
    case EAI_MEMORY:
        return "Memory allocation failure";
    case EAI_FAMILY:
        return "Address family not supported";
    }
    return "Unknown error";
}

#endif

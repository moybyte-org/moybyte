// The setup access point's captive portal (moy_net.h): one DNS responder that
// answers every name with the access point's own address, so a phone's
// connectivity probe lands on the setup form and the form opens by itself.
// The IDF's DHCP server already offers the AP as the DNS server
// (CONFIG_LWIP_DHCPS_ADD_DNS), so answering on :53 is enough.
//
// moy_dns_reply reads untrusted bytes (fuzz_net.c runs it): a reply (QR=1), an
// opcode that is not QUERY, anything but exactly one question, a compression
// pointer inside the question, or a truncated one is dropped. A question it
// will not answer (AAAA, a class that is not IN) is answered NOERROR with no
// records, never NXDOMAIN, so a phone that asks AAAA first falls back to A.
// TTL 0: nothing this says about a name may outlive the phone's stay.
//
// The responder (start, poll, stop) is optional at every step: a bind that
// fails leaves the form served at its address, and a datagram that does not
// parse is dropped.

#include <string.h>

#include "moy_net.h"

size_t moy_dns_reply(const uint8_t *q, size_t n, uint32_t ip, uint8_t *out,
                     size_t cap) {
    if (q == NULL || n < 17 || n > MOY_DNS_QUERY_MAX) {
        return 0;
    }
    uint8_t flags = q[2];
    if ((flags & 0x80) || ((flags >> 3) & 0x0F) || (q[4] << 8 | q[5]) != 1) {
        return 0;
    }
    size_t i = 12;
    for (;;) {
        if (i >= n) {
            return 0;                   // ran off the end mid-name
        }
        uint8_t len = q[i];
        if (len == 0) {
            i++;
            break;
        }
        if (len & 0xC0) {
            return 0;                   // a pointer or a reserved length
        }
        i += (size_t)len + 1u;
    }
    if (i + 4 > n) {
        return 0;
    }
    int qtype = q[i] << 8 | q[i + 1], qclass = q[i + 2] << 8 | q[i + 3];
    size_t end = i + 4;
    int answer = qtype == 1 && qclass == 1 && ip != 0;
    size_t need = end + (answer ? 16u : 0u);
    if (need > cap) {
        return 0;
    }
    memcpy(out, q, end);                // the header and the question, verbatim
    out[2] = (uint8_t)(0x84 | (flags & 0x01));   // QR, AA, RD echoed
    out[3] = 0;
    out[6] = 0;
    out[7] = answer ? 1 : 0;
    memset(out + 8, 0, 4);
    if (answer) {
        static const uint8_t rr[12] = {0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 0, 0, 4};
        memcpy(out + end, rr, sizeof(rr));
        out[end + 12] = (uint8_t)(ip & 0xff);   // network order, a.b.c.d
        out[end + 13] = (uint8_t)((ip >> 8) & 0xff);
        out[end + 14] = (uint8_t)((ip >> 16) & 0xff);
        out[end + 15] = (uint8_t)(ip >> 24);
    }
    return need;
}

typedef struct {
    int fd;
    uint16_t port;
    uint32_t ip;
    uint32_t answered;
    uint8_t q[MOY_DNS_QUERY_MAX], a[MOY_DNS_QUERY_MAX + 16];
} dns_t;

static dns_t *s_dns;

int moy_dns_start(uint32_t ip, uint16_t port) {
    moy_dns_stop();
    s_dns = moy_net_alloc(sizeof(dns_t));
    if (s_dns == NULL) {
        return 12;
    }
    s_dns->ip = ip;
    s_dns->fd = moy_udp_listen(port);
    if (s_dns->fd < 0) {
        int e = -s_dns->fd;
        moy_net_free(s_dns);
        s_dns = NULL;
        return e;
    }
    s_dns->port = moy_udp_port(s_dns->fd);
    return 0;
}

uint16_t moy_dns_port(void) {
    return s_dns != NULL ? s_dns->port : 0;
}

int moy_dns_poll(void) {
    if (s_dns == NULL) {
        return 0;
    }
    int served = 0;
    for (int k = 0; k < MOY_DNS_PER_POLL; k++) {
        uint8_t from[MOY_UDP_ADDR];
        int got = moy_udp_recv(s_dns->fd, s_dns->q, sizeof(s_dns->q), from);
        if (got <= 0) {
            break;
        }
        size_t r = moy_dns_reply(s_dns->q, (size_t)got, s_dns->ip, s_dns->a, sizeof(s_dns->a));
        if (r > 0 && moy_udp_send(s_dns->fd, s_dns->a, r, from) == 0) {
            served++;
        }
    }
    s_dns->answered += (uint32_t)served;
    return served;
}

void moy_dns_stop(void) {
    if (s_dns != NULL) {
        moy_http_close(s_dns->fd);
        moy_net_free(s_dns);
        s_dns = NULL;
    }
}

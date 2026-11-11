// Internet addresses as lwIP lays them out (sys/socket.h says why).
#ifndef _MOY_NETINET_IN_H
#define _MOY_NETINET_IN_H

#include <stdint.h>
#include <sys/socket.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef uint16_t in_port_t;
typedef uint32_t in_addr_t;

struct in_addr {
    in_addr_t s_addr;
};

struct in6_addr {
    union {
        uint32_t u32_addr[4];
        uint8_t u8_addr[16];
    } un;
};
#define s6_addr un.u8_addr

struct sockaddr_in {
    uint8_t sin_len;
    sa_family_t sin_family;
    in_port_t sin_port;
    struct in_addr sin_addr;
    char sin_zero[8];
};

struct sockaddr_in6 {
    uint8_t sin6_len;
    sa_family_t sin6_family;
    in_port_t sin6_port;
    uint32_t sin6_flowinfo;
    struct in6_addr sin6_addr;
    uint32_t sin6_scope_id;
};

#define IPPROTO_IP 0
#define IPPROTO_ICMP 1
#define IPPROTO_TCP 6
#define IPPROTO_UDP 17
#define IPPROTO_IPV6 41
#define IPPROTO_ICMPV6 58
#define IPPROTO_RAW 255

#define IP_TOS 1
#define IP_TTL 2
#define IP_ADD_MEMBERSHIP 3
#define IP_DROP_MEMBERSHIP 4
#define IP_MULTICAST_TTL 5
#define IP_MULTICAST_IF 6
#define IP_MULTICAST_LOOP 7
#define IPV6_V6ONLY 27

#define INADDR_ANY ((in_addr_t)0x00000000UL)
#define INADDR_LOOPBACK ((in_addr_t)0x7f000001UL)
#define INADDR_BROADCAST ((in_addr_t)0xffffffffUL)
#define INADDR_NONE ((in_addr_t)0xffffffffUL)

#define INET_ADDRSTRLEN 16
#define INET6_ADDRSTRLEN 46

#define IN6ADDR_ANY_INIT {{{0, 0, 0, 0}}}

static inline uint16_t htons(uint16_t v) { return __builtin_bswap16(v); }
static inline uint16_t ntohs(uint16_t v) { return __builtin_bswap16(v); }
static inline uint32_t htonl(uint32_t v) { return __builtin_bswap32(v); }
static inline uint32_t ntohl(uint32_t v) { return __builtin_bswap32(v); }

#ifdef __cplusplus
}
#endif

#endif

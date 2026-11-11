// Name lookup as lwIP answers it (sys/socket.h says why).
#ifndef _MOY_NETDB_H
#define _MOY_NETDB_H

#include <netinet/in.h>
#include <sys/socket.h>

#ifdef __cplusplus
extern "C" {
#endif

struct addrinfo {
    int ai_flags;
    int ai_family;
    int ai_socktype;
    int ai_protocol;
    socklen_t ai_addrlen;
    struct sockaddr *ai_addr;
    char *ai_canonname;
    struct addrinfo *ai_next;
};

#define AI_PASSIVE 0x01
#define AI_CANONNAME 0x02
#define AI_NUMERICHOST 0x04
#define AI_NUMERICSERV 0x08
#define AI_V4MAPPED 0x10
#define AI_ALL 0x20
#define AI_ADDRCONFIG 0x40

#define EAI_NONAME 200
#define EAI_SERVICE 201
#define EAI_FAIL 202
#define EAI_MEMORY 203
#define EAI_FAMILY 204

#define HOST_NOT_FOUND 210
#define NO_DATA 211
#define NO_RECOVERY 212
#define TRY_AGAIN 213

int getaddrinfo(const char *node, const char *service, const struct addrinfo *hints,
                struct addrinfo **res);
void freeaddrinfo(struct addrinfo *ai);
const char *gai_strerror(int code);

#ifdef __cplusplus
}
#endif

#endif

// Address text both ways (sys/socket.h says why); the SDK's C library
// carries these, no row of the OS table.
#ifndef _MOY_ARPA_INET_H
#define _MOY_ARPA_INET_H

#include <netinet/in.h>

#ifdef __cplusplus
extern "C" {
#endif

in_addr_t inet_addr(const char *text);
int inet_aton(const char *text, struct in_addr *out);
char *inet_ntoa(struct in_addr in);
const char *inet_ntop(int af, const void *src, char *dst, socklen_t size);
int inet_pton(int af, const char *src, void *dst);

#ifdef __cplusplus
}
#endif

#endif

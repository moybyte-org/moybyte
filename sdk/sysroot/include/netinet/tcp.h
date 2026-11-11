// TCP's socket options as lwIP numbers them (sys/socket.h says why).
#ifndef _MOY_NETINET_TCP_H
#define _MOY_NETINET_TCP_H

#define TCP_NODELAY 0x01
#define TCP_KEEPALIVE 0x02
#define TCP_KEEPIDLE 0x03
#define TCP_KEEPINTVL 0x04
#define TCP_KEEPCNT 0x05

#endif

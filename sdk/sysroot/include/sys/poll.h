// poll as the IDF declares it (newlib/platform_include/sys/poll.h): the OS
// table's poll row takes these values.
#ifndef _MOY_SYS_POLL_H
#define _MOY_SYS_POLL_H

#ifdef __cplusplus
extern "C" {
#endif

#define POLLIN (1u << 0)
#define POLLRDNORM (1u << 1)
#define POLLRDBAND (1u << 2)
#define POLLPRI (POLLRDBAND)
#define POLLOUT (1u << 3)
#define POLLWRNORM (POLLOUT)
#define POLLWRBAND (1u << 4)
#define POLLERR (1u << 5)
#define POLLHUP (1u << 6)
#define POLLNVAL (1u << 7)

struct pollfd {
    int fd;
    short events;
    short revents;
};

typedef unsigned int nfds_t;

int poll(struct pollfd *fds, nfds_t n, int timeout);

#ifdef __cplusplus
}
#endif

#endif

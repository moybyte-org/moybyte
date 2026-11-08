// nc: a small netcat. It connects to HOST PORT, or with -l listens on PORT
// for one connection, then copies standard input to the socket and the
// socket to standard output until the peer closes; at the end of its input
// it shuts its sending side down. A plain POSIX program.
//
//   nc [-v] [-w secs] host port
//   nc -l [-v] [-w secs] [-s addr] [-p] port
#include <errno.h>
#include <netdb.h>
#include <netinet/in.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static int verbose;

static void usage(const char *me) {
    fprintf(stderr, "usage: %s [-v] [-w secs] host port\n"
                    "       %s -l [-v] [-w secs] [-s addr] [-p] port\n", me, me);
    exit(2);
}

static int dial(const char *host, const char *port) {
    struct addrinfo hints, *res, *ai;
    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    int e = getaddrinfo(host, port, &hints, &res);
    if (e != 0) {
        fprintf(stderr, "nc: %s: %s\n", host, gai_strerror(e));
        return -1;
    }
    int fd = -1;
    for (ai = res; ai != NULL; ai = ai->ai_next) {
        fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (fd < 0) {
            continue;
        }
        if (connect(fd, ai->ai_addr, ai->ai_addrlen) == 0) {
            break;
        }
        close(fd);
        fd = -1;
    }
    if (fd < 0) {
        fprintf(stderr, "nc: connect to %s port %s: %s\n", host, port, strerror(errno));
    } else if (verbose) {
        fprintf(stderr, "Connection to %s %s port succeeded!\n", host, port);
    }
    freeaddrinfo(res);
    return fd;
}

static int serve(const char *addr, const char *port) {
    char *end;
    long p = strtol(port, &end, 10);
    if (*port == '\0' || *end != '\0' || p < 0 || p > 65535) {
        fprintf(stderr, "nc: port number invalid: %s\n", port);
        return -1;
    }
    int ls = socket(AF_INET, SOCK_STREAM, 0);
    if (ls < 0) {
        perror("nc: socket");
        return -1;
    }
    int on = 1;
    setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &on, sizeof on);
    struct sockaddr_in sin;
    memset(&sin, 0, sizeof sin);
    sin.sin_family = AF_INET;
    sin.sin_port = htons((unsigned short)p);
    sin.sin_addr.s_addr = htonl(INADDR_ANY);
    if (addr != NULL) {
        struct addrinfo hints, *res;
        memset(&hints, 0, sizeof hints);
        hints.ai_family = AF_INET;
        hints.ai_flags = AI_NUMERICHOST;
        if (getaddrinfo(addr, NULL, &hints, &res) != 0) {
            fprintf(stderr, "nc: bad address %s\n", addr);
            close(ls);
            return -1;
        }
        sin.sin_addr = ((struct sockaddr_in *)res->ai_addr)->sin_addr;
        freeaddrinfo(res);
    }
    if (bind(ls, (struct sockaddr *)&sin, sizeof sin) != 0 || listen(ls, 1) != 0) {
        perror("nc: bind");
        close(ls);
        return -1;
    }
    if (verbose) {
        socklen_t n = sizeof sin;
        getsockname(ls, (struct sockaddr *)&sin, &n);
        fprintf(stderr, "Listening on port %d\n", ntohs(sin.sin_port));
    }
    struct sockaddr_in peer;
    socklen_t n = sizeof peer;
    int fd = accept(ls, (struct sockaddr *)&peer, &n);
    close(ls);
    if (fd < 0) {
        perror("nc: accept");
        return -1;
    }
    if (verbose) {
        fprintf(stderr, "Connection received\n");
    }
    return fd;
}

static int put_all(int fd, const char *p, ssize_t n) {
    while (n > 0) {
        ssize_t w = write(fd, p, (size_t)n);
        if (w <= 0) {
            return -1;
        }
        p += w;
        n -= w;
    }
    return 0;
}

// Both directions until the network side ends: 0, or 1 on an error or the
// -w idle time.
static int pump(int net, int idle_ms) {
    struct pollfd fds[2] = { { 0, POLLIN, 0 }, { net, POLLIN, 0 } };
    char buf[1024];
    for (;;) {
        int r = poll(fds, 2, idle_ms > 0 ? idle_ms : -1);
        if (r < 0) {
            if (errno == EINTR) {
                return 1;
            }
            perror("nc: poll");
            return 1;
        }
        if (r == 0) {
            return 1;
        }
        if (fds[0].revents) {
            ssize_t n = read(0, buf, sizeof buf);
            if (n <= 0) {
                shutdown(net, SHUT_WR);
                fds[0].fd = -1;
            } else if (put_all(net, buf, n) != 0) {
                perror("nc: write");
                return 1;
            }
        }
        if (fds[1].revents) {
            ssize_t n = read(net, buf, sizeof buf);
            if (n < 0) {
                perror("nc: read");
                return 1;
            }
            if (n == 0) {
                return 0;
            }
            if (put_all(1, buf, n) != 0) {
                return 1;
            }
        }
    }
}

int main(int argc, char **argv) {
    int listen_mode = 0, idle_ms = 0, c;
    const char *addr = NULL, *port = NULL;
    while ((c = getopt(argc, argv, "lvw:s:p:")) != -1) {
        switch (c) {
        case 'l':
            listen_mode = 1;
            break;
        case 'v':
            verbose = 1;
            break;
        case 'w':
            idle_ms = atoi(optarg) * 1000;
            break;
        case 's':
            addr = optarg;
            break;
        case 'p':
            port = optarg;
            break;
        default:
            usage(argv[0]);
        }
    }
    int left = argc - optind;
    int fd;
    if (listen_mode) {
        if (port == NULL && left == 1) {
            port = argv[optind];
        } else if (port == NULL || left != 0) {
            usage(argv[0]);
        }
        fd = serve(addr, port);
    } else {
        if (left != 2) {
            usage(argv[0]);
        }
        fd = dial(argv[optind], argv[optind + 1]);
    }
    if (fd < 0) {
        return 1;
    }
    int status = pump(fd, idle_ms);
    close(fd);
    return status;
}

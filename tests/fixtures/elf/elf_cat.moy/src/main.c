// cat: each file named, or stdin, to stdout. A plain POSIX program over
// open, read, write and close.
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>

static void complain(const char *name, int e) {
    const char *why = e == ENOENT ? "No such file or directory"
                    : e == EACCES ? "Permission denied"
                    : e == EISDIR ? "Is a directory" : "I/O error";
    write(2, "cat: ", 5);
    write(2, name, strlen(name));
    write(2, ": ", 2);
    write(2, why, strlen(why));
    write(2, "\n", 1);
}

static int copy(int fd) {
    char buf[4096];
    ssize_t n;
    while ((n = read(fd, buf, sizeof buf)) > 0) {
        for (ssize_t at = 0; at < n;) {
            ssize_t w = write(1, buf + at, (size_t)(n - at));
            if (w <= 0) {
                return 1;
            }
            at += w;
        }
    }
    return n < 0;
}

int main(int argc, char **argv) {
    int status = 0;
    if (argc < 2) {
        return copy(0);
    }
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-") == 0) {
            status |= copy(0);
            continue;
        }
        int fd = open(argv[i], O_RDONLY);
        if (fd < 0) {
            complain(argv[i], errno);
            status = 1;
            continue;
        }
        status |= copy(fd);
        close(fd);
    }
    return status;
}

// What newlib's headers reach a library for (sdk/README.md): the ctype
// table its macros index, the ctype calls, case-blind compares, strtok and
// strerror. glibc's are the host's own: its ctype is a per-thread table and
// its strerror reads no stream.
#include "lc.h"

#if defined(LC_NEWLIB)

#include <ctype.h>
#include <string.h>

// _ctype_[1 + c] is c's class bits (_U _L _N _S _P _C _X _B), [0] EOF's.
LC_API const char _ctype_[1 + 256] = {
    0,
    32, 32, 32, 32, 32, 32, 32, 32, 32, 40, 40, 40, 40, 40, 32, 32,
    32, 32, 32, 32, 32, 32, 32, 32, 32, 32, 32, 32, 32, 32, 32, 32,
    136, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16,
    68, 68, 68, 68, 68, 68, 68, 68, 68, 68, 16, 16, 16, 16, 16, 16,
    16, 65, 65, 65, 65, 65, 65, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 16, 16, 16, 16, 16,
    16, 66, 66, 66, 66, 66, 66, 2, 2, 2, 2, 2, 2, 2, 2, 2,
    2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 16, 16, 16, 16, 32,
};

#define CL(c) (_ctype_[1 + (unsigned char)(c)])

#undef isalpha
#undef isdigit
#undef isalnum
#undef isspace
#undef isupper
#undef islower
#undef isxdigit
#undef ispunct
#undef isprint
#undef isgraph
#undef iscntrl
#undef isblank
#undef isascii
#undef toupper
#undef tolower

LC_API int isalpha(int c) { return c >= 0 && c < 256 && (CL(c) & (_U | _L)); }
LC_API int isdigit(int c) { return c >= '0' && c <= '9'; }
LC_API int isalnum(int c) { return c >= 0 && c < 256 && (CL(c) & (_U | _L | _N)); }
LC_API int isspace(int c) { return c >= 0 && c < 256 && (CL(c) & _S); }
LC_API int isupper(int c) { return c >= 'A' && c <= 'Z'; }
LC_API int islower(int c) { return c >= 'a' && c <= 'z'; }
LC_API int isxdigit(int c) { return c >= 0 && c < 256 && (CL(c) & _X); }
LC_API int ispunct(int c) { return c >= 0 && c < 256 && (CL(c) & _P); }
LC_API int isprint(int c) { return c >= 0x20 && c < 0x7f; }
LC_API int isgraph(int c) { return c > 0x20 && c < 0x7f; }
LC_API int iscntrl(int c) { return c >= 0 && c < 256 && (CL(c) & _C); }
LC_API int isblank(int c) { return c == ' ' || c == '\t'; }
LC_API int isascii(int c) { return c >= 0 && c < 0x80; }
LC_API int toupper(int c) { return c >= 'a' && c <= 'z' ? c - 'a' + 'A' : c; }
LC_API int tolower(int c) { return c >= 'A' && c <= 'Z' ? c - 'A' + 'a' : c; }

LC_API int strcasecmp(const char *a, const char *b) {
    for (;; a++, b++) {
        int x = tolower((unsigned char)*a), y = tolower((unsigned char)*b);
        if (x != y || x == 0) {
            return x - y;
        }
    }
}

LC_API int strncasecmp(const char *a, const char *b, size_t n) {
    for (; n; n--, a++, b++) {
        int x = tolower((unsigned char)*a), y = tolower((unsigned char)*b);
        if (x != y || x == 0) {
            return x - y;
        }
    }
    return 0;
}

LC_API char *strtok(char *s, const char *sep) {
    static char *s_last;
    return strtok_r(s, sep, &s_last);
}

static const struct {
    int code;
    const char *text;
} k_errors[] = {
    { 0, "Success" },
    { EPERM, "Not owner" },
    { ENOENT, "No such file or directory" },
    { EINTR, "Interrupted system call" },
    { EIO, "I/O error" },
    { EBADF, "Bad file number" },
    { EAGAIN, "No more processes" },
    { ENOMEM, "Not enough space" },
    { EACCES, "Permission denied" },
    { EFAULT, "Bad address" },
    { EBUSY, "Device or resource busy" },
    { EEXIST, "File exists" },
    { ENOTDIR, "Not a directory" },
    { EISDIR, "Is a directory" },
    { EINVAL, "Invalid argument" },
    { EMFILE, "Too many open files in process" },
    { ENOTTY, "Not a character device" },
    { EFBIG, "File too large" },
    { ENOSPC, "No space left on device" },
    { ESPIPE, "Illegal seek" },
    { EROFS, "Read-only file system" },
    { EPIPE, "Broken pipe" },
    { EDOM, "Mathematics argument out of domain of function" },
    { ERANGE, "Result too large" },
    { ENAMETOOLONG, "File or path name too long" },
    { ENOSYS, "Function not implemented" },
    { ENOTEMPTY, "Directory not empty" },
    { ENOTSOCK, "Socket operation on non-socket" },
    { EADDRINUSE, "Address already in use" },
    { EADDRNOTAVAIL, "Address not available" },
    { ENETDOWN, "Network interface is not configured" },
    { ENETUNREACH, "Network is unreachable" },
    { ECONNABORTED, "Software caused connection abort" },
    { ECONNRESET, "Connection reset by peer" },
    { ENOBUFS, "No buffer space available" },
    { EISCONN, "Socket is already connected" },
    { ENOTCONN, "Socket is not connected" },
    { ETIMEDOUT, "Connection timed out" },
    { ECONNREFUSED, "Connection refused" },
    { EHOSTUNREACH, "Host is unreachable" },
    { EALREADY, "Socket already connected" },
    { EINPROGRESS, "Connection already in progress" },
    { EAFNOSUPPORT, "Address family not supported by protocol family" },
    { EPROTONOSUPPORT, "Unknown protocol" },
    { EOPNOTSUPP, "Operation not supported on socket" },
    { EMSGSIZE, "Message too long" },
};

LC_API char *strerror(int e) {
    for (size_t i = 0; i < sizeof k_errors / sizeof k_errors[0]; i++) {
        if (k_errors[i].code == e) {
            return (char *)k_errors[i].text;
        }
    }
    return (char *)"Unknown error";
}

LC_API int strerror_r(int e, char *buf, size_t n) {
    const char *t = strerror(e);
    if (n == 0) {
        return ERANGE;
    }
    strncpy(buf, t, n - 1);
    buf[n - 1] = '\0';
    return strlen(t) < n ? 0 : ERANGE;
}

#endif

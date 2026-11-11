// The SDK's C library, inside (sdk/README.md): what every file of it shares.
//
// It is compiled against the module's own C library headers -- the IDF's
// newlib on a board, glibc on the host -- so a program's FILE, ctype and
// errno are the types those headers declare. Everything here is hidden: a
// module's calls bind to it, and the module exports nothing.
#ifndef MOY_LC_H
#define MOY_LC_H

#define _DEFAULT_SOURCE 1
#undef _FORTIFY_SOURCE

#include <errno.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#if defined(__NEWLIB__)
#define LC_NEWLIB 1
#elif defined(__GLIBC__)
#define LC_GLIBC 1
#else
#error "the SDK's C library knows newlib and glibc"
#endif

#define LC_API __attribute__((visibility("hidden")))

// `sym`, hidden, as another name of `impl`: for a call whose name a C
// library's headers may rewrite (glibc's scanf is __isoc99_scanf, its
// strtol __isoc23_strtol in C23), so every spelling binds here.
#define LC_EXPORT(sym, impl) \
    __asm__(".globl " #sym "\n.hidden " #sym "\n.set " #sym ", " #impl)

// A stream: the C library's FILE first, so a FILE * is one of these. The
// FILE's own buffer fields stay empty, so its headers' inline getc and putc
// fall through to the calls this library defines (__srget_r and __swbuf_r
// on newlib, __uflow and __overflow on glibc).
#define LC_RD 1
#define LC_WR 2
#define LC_EOF 4
#define LC_ERR 8
#define LC_OWN 16           // the record is malloc's (fopen's), not static
#define LC_APPEND 32
#define LC_OWNBUF 64        // the buffer is malloc's

typedef struct lc_file {
    FILE f;
    int fd;
    int flags;
    int mode;               // _IOFBF, _IOLBF or _IONBF
    int unget;              // a pushed-back byte, or -1
    unsigned char *buf;
    size_t cap;
    size_t rpos, rlen;      // reading: the buffer's unread bytes
    size_t wlen;            // writing: the buffer's unwritten bytes
    struct lc_file *next;
} lc_file_t;

#define LC_F(fp) ((lc_file_t *)(fp))

LC_API int lc_getc(lc_file_t *f);
LC_API int lc_putc(int c, lc_file_t *f);
LC_API size_t lc_write(lc_file_t *f, const void *p, size_t n);
LC_API int lc_flush(lc_file_t *f);
LC_API void lc_flush_all(void);

// The formatter: `out(arg, p, n)` takes each run of output; answers the
// count, or -1 when out failed.
typedef int (*lc_out_fn)(void *arg, const char *p, size_t n);
LC_API int lc_format(lc_out_fn out, void *arg, const char *fmt, va_list ap);

// The scanner, over `get` (a byte, or -1 at the end) and `unget`.
typedef struct {
    int (*get)(void *arg);
    void (*unget)(void *arg, int c);
    void *arg;
} lc_in_t;
LC_API int lc_scan(lc_in_t *in, const char *fmt, va_list ap);

// The number parsers every reader shares.
LC_API unsigned long long lc_parse_u(const char *s, char **end, int base, int *neg, int *over);
LC_API double lc_strtod(const char *s, char **end);

// The exit path: atexit's functions, then every stream flushed.
LC_API void __moy_exit(void);

#endif

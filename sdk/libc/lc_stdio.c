// stdio over the OS table's descriptors (sdk/README.md): the three standard
// streams, fopen's, their buffers, and every call that reads or writes one.
#include "lc.h"

#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <unistd.h>

#if defined(LC_NEWLIB)
#include <sys/reent.h>
#define LC_FEOF __SEOF
#define LC_FERR __SERR
#else
#define LC_FEOF 0x10        // glibc's _IO_EOF_SEEN
#define LC_FERR 0x20        // _IO_ERR_SEEN
#endif

// The headers' macro forms, which these files define as calls.
#undef getc
#undef putc
#undef getchar
#undef putchar
#undef getc_unlocked
#undef putc_unlocked
#undef getchar_unlocked
#undef putchar_unlocked
#undef feof
#undef ferror
#undef clearerr
#undef fileno

#define STD_BUF 512
#define FILE_BUF 1024

static unsigned char s_in_buf[STD_BUF], s_out_buf[STD_BUF];
static lc_file_t s_in = { .fd = 0, .flags = LC_RD, .mode = -1, .unget = -1,
                          .buf = s_in_buf, .cap = STD_BUF };
static lc_file_t s_out = { .fd = 1, .flags = LC_WR, .mode = -1, .unget = -1,
                           .buf = s_out_buf, .cap = STD_BUF };
static lc_file_t s_err = { .fd = 2, .flags = LC_WR, .mode = _IONBF, .unget = -1 };
static lc_file_t *s_open;          // fopen's streams

#if defined(LC_NEWLIB)
// newlib's stdin, stdout and stderr are the reentrancy record's fields, its
// errno the OS table's (__errno).
static struct _reent s_reent = { ._stdin = &s_in.f, ._stdout = &s_out.f, ._stderr = &s_err.f };

LC_API struct _reent *__getreent(void) {
    return &s_reent;
}
#else
LC_API FILE *stdin = &s_in.f;
LC_API FILE *stdout = &s_out.f;
LC_API FILE *stderr = &s_err.f;
#endif

static void mark(lc_file_t *f, int bit) {
    f->flags |= bit;
    f->f._flags |= bit == LC_EOF ? LC_FEOF : LC_FERR;
}

// A standard stream's buffering, decided at its first use: line by line
// to a terminal, in blocks otherwise.
static void settle(lc_file_t *f) {
    if (f->mode < 0) {
        f->mode = isatty(f->fd) ? _IOLBF : _IOFBF;
    }
}

static int fill(lc_file_t *f) {
    if (f == &s_in) {
        lc_flush(&s_out);
    }
    if (f->cap == 0) {
        f->buf = malloc(FILE_BUF);
        if (f->buf == NULL) {
            mark(f, LC_ERR);
            return -1;
        }
        f->cap = FILE_BUF;
        f->flags |= LC_OWNBUF;
    }
    ssize_t n = read(f->fd, f->buf, f->mode == _IONBF ? 1 : f->cap);
    if (n <= 0) {
        mark(f, n == 0 ? LC_EOF : LC_ERR);
        return -1;
    }
    f->rpos = 0;
    f->rlen = (size_t)n;
    return 0;
}

static int to_read(lc_file_t *f) {
    if (!(f->flags & LC_RD)) {
        mark(f, LC_ERR);
        errno = EBADF;
        return -1;
    }
    if (f->wlen && lc_flush(f) != 0) {
        return -1;
    }
    settle(f);
    return 0;
}

LC_API int lc_getc(lc_file_t *f) {
    if (f->unget >= 0) {
        int c = f->unget;
        f->unget = -1;
        return c;
    }
    if (f->rpos >= f->rlen) {
        if (to_read(f) != 0 || fill(f) != 0) {
            return EOF;
        }
    }
    return f->buf[f->rpos++];
}

static int raw_write(lc_file_t *f, const unsigned char *p, size_t n) {
    while (n) {
        ssize_t w = write(f->fd, p, n);
        if (w <= 0) {
            mark(f, LC_ERR);
            return -1;
        }
        p += w;
        n -= (size_t)w;
    }
    return 0;
}

LC_API int lc_flush(lc_file_t *f) {
    if (f->wlen) {
        size_t n = f->wlen;
        f->wlen = 0;
        if (raw_write(f, f->buf, n) != 0) {
            return EOF;
        }
    }
    if (f->rpos < f->rlen || f->unget >= 0) {
        // What was read ahead goes back, so the descriptor stands where the
        // stream does.
        off_t back = (off_t)(f->rlen - f->rpos) + (f->unget >= 0);
        lseek(f->fd, -back, SEEK_CUR);
    }
    f->rpos = f->rlen = 0;
    f->unget = -1;
    return 0;
}

static int to_write(lc_file_t *f) {
    if (!(f->flags & LC_WR)) {
        mark(f, LC_ERR);
        errno = EBADF;
        return -1;
    }
    if (f->rpos < f->rlen || f->unget >= 0) {
        lc_flush(f);
    }
    settle(f);
    if (f->mode != _IONBF && f->cap == 0) {
        f->buf = malloc(FILE_BUF);
        if (f->buf != NULL) {
            f->cap = FILE_BUF;
            f->flags |= LC_OWNBUF;
        } else {
            f->mode = _IONBF;
        }
    }
    return 0;
}

LC_API size_t lc_write(lc_file_t *f, const void *p, size_t n) {
    if (n == 0) {
        return 0;
    }
    if (to_write(f) != 0) {
        return 0;
    }
    const unsigned char *b = p;
    if (f->mode == _IONBF) {
        return raw_write(f, b, n) == 0 ? n : 0;
    }
    size_t done = 0;
    while (done < n) {
        if (f->wlen == f->cap && lc_flush(f) != 0) {
            return done;
        }
        size_t k = n - done;
        if (k > f->cap - f->wlen) {
            k = f->cap - f->wlen;
        }
        memcpy(f->buf + f->wlen, b + done, k);
        f->wlen += k;
        done += k;
    }
    if (f->mode == _IOLBF && memchr(b, '\n', n) != NULL && lc_flush(f) != 0) {
        return done;
    }
    return done;
}

LC_API int lc_putc(int c, lc_file_t *f) {
    unsigned char b = (unsigned char)c;
    return lc_write(f, &b, 1) == 1 ? b : EOF;
}

LC_API void lc_flush_all(void) {
    lc_flush(&s_out);
    lc_flush(&s_err);
    for (lc_file_t *f = s_open; f != NULL; f = f->next) {
        if (f->flags & LC_WR) {
            lc_flush(f);
        }
    }
}

// -- the headers' slow paths ----------------------------------------------------------

#if defined(LC_NEWLIB)
LC_API int __srget_r(struct _reent *r, FILE *fp) {
    (void)r;
    fp->_r = 0;
    return lc_getc(LC_F(fp));
}

LC_API int __swbuf_r(struct _reent *r, int c, FILE *fp) {
    (void)r;
    fp->_w = 0;
    return lc_putc(c, LC_F(fp));
}
#else
LC_API int __uflow(FILE *fp) {
    return lc_getc(LC_F(fp));
}

LC_API int __underflow(FILE *fp) {
    int c = lc_getc(LC_F(fp));
    if (c != EOF) {
        LC_F(fp)->unget = c;
    }
    return c;
}

LC_API int __overflow(FILE *fp, int c) {
    return c == EOF ? (lc_flush(LC_F(fp)) == 0 ? 0 : EOF) : lc_putc(c, LC_F(fp));
}
#endif

// -- opening and closing ------------------------------------------------------------

static int parse_mode(const char *m, int *rw) {
    int fl;
    switch (*m) {
    case 'r':
        fl = O_RDONLY;
        *rw = LC_RD;
        break;
    case 'w':
        fl = O_WRONLY | O_CREAT | O_TRUNC;
        *rw = LC_WR;
        break;
    case 'a':
        fl = O_WRONLY | O_CREAT | O_APPEND;
        *rw = LC_WR | LC_APPEND;
        break;
    default:
        return -1;
    }
    for (m++; *m; m++) {
        if (*m == '+') {
            fl = (fl & ~(O_RDONLY | O_WRONLY)) | O_RDWR;
            *rw |= LC_RD | LC_WR;
        } else if (*m == 'x') {
            fl |= O_EXCL;
        }
    }
    return fl;
}

static FILE *adopt(int fd, int rw) {
    lc_file_t *f = calloc(1, sizeof *f);
    if (f == NULL) {
        return NULL;
    }
    f->fd = fd;
    f->flags = rw | LC_OWN;
    f->mode = _IOFBF;
    f->unget = -1;
#if defined(LC_NEWLIB)
    f->f._file = (short)fd;
    f->f._flags = 1;
#else
    f->f._fileno = fd;
#endif
    f->next = s_open;
    s_open = f;
    return &f->f;
}

LC_API FILE *fopen(const char *path, const char *mode) {
    int rw;
    int fl = parse_mode(mode, &rw);
    if (fl < 0) {
        errno = EINVAL;
        return NULL;
    }
    int fd = open(path, fl, 0666);
    if (fd < 0) {
        return NULL;
    }
    FILE *fp = adopt(fd, rw);
    if (fp == NULL) {
        close(fd);
    }
    return fp;
}

LC_API FILE *fdopen(int fd, const char *mode) {
    int rw;
    if (parse_mode(mode, &rw) < 0) {
        errno = EINVAL;
        return NULL;
    }
    return adopt(fd, rw);
}

static void drop_buf(lc_file_t *f) {
    if (f->flags & LC_OWNBUF) {
        free(f->buf);
        f->flags &= ~LC_OWNBUF;
    }
    f->buf = NULL;
    f->cap = 0;
}

LC_API int fclose(FILE *fp) {
    lc_file_t *f = LC_F(fp);
    int r = (f->flags & LC_WR) && lc_flush(f) != 0 ? EOF : 0;
    if (close(f->fd) != 0) {
        r = EOF;
    }
    if (!(f->flags & LC_OWN)) {
        f->flags &= ~(LC_RD | LC_WR);
        return r;
    }
    for (lc_file_t **at = &s_open; *at != NULL; at = &(*at)->next) {
        if (*at == f) {
            *at = f->next;
            break;
        }
    }
    drop_buf(f);
    free(f);
    return r;
}

LC_API FILE *freopen(const char *path, const char *mode, FILE *fp) {
    lc_file_t *f = LC_F(fp);
    int rw;
    int fl = parse_mode(mode, &rw);
    if (fl < 0 || path == NULL) {
        errno = EINVAL;
        return NULL;
    }
    lc_flush(f);
    close(f->fd);
    int fd = open(path, fl, 0666);
    if (fd < 0) {
        return NULL;
    }
    f->fd = fd;
    f->flags = (f->flags & (LC_OWN | LC_OWNBUF)) | rw;
    f->f._flags &= ~(LC_FEOF | LC_FERR);
    return fp;
}

LC_API int fflush(FILE *fp) {
    if (fp == NULL) {
        lc_flush_all();
        return 0;
    }
    return lc_flush(LC_F(fp));
}

LC_API int setvbuf(FILE *fp, char *buf, int mode, size_t size) {
    lc_file_t *f = LC_F(fp);
    lc_flush(f);
    if (f != &s_in && f != &s_out) {
        drop_buf(f);
    }
    f->mode = mode;
    if (mode != _IONBF && buf != NULL && size > 0) {
        f->buf = (unsigned char *)buf;
        f->cap = size;
    } else if (mode == _IONBF && f != &s_in && f != &s_out) {
        f->cap = 0;
    }
    return 0;
}

LC_API void setbuf(FILE *fp, char *buf) {
    setvbuf(fp, buf, buf != NULL ? _IOFBF : _IONBF, BUFSIZ);
}

// -- bytes ------------------------------------------------------------------------

LC_API int fgetc(FILE *fp) {
    return lc_getc(LC_F(fp));
}

LC_API int getc(FILE *fp) {
    return lc_getc(LC_F(fp));
}

LC_API int getc_unlocked(FILE *fp) {
    return lc_getc(LC_F(fp));
}

LC_API int getchar(void) {
    return lc_getc(&s_in);
}

LC_API int getchar_unlocked(void) {
    return lc_getc(&s_in);
}

LC_API int fputc(int c, FILE *fp) {
    return lc_putc(c, LC_F(fp));
}

LC_API int putc(int c, FILE *fp) {
    return lc_putc(c, LC_F(fp));
}

LC_API int putc_unlocked(int c, FILE *fp) {
    return lc_putc(c, LC_F(fp));
}

LC_API int putchar(int c) {
    return lc_putc(c, &s_out);
}

LC_API int putchar_unlocked(int c) {
    return lc_putc(c, &s_out);
}

LC_API int ungetc(int c, FILE *fp) {
    lc_file_t *f = LC_F(fp);
    if (c == EOF || f->unget >= 0) {
        return EOF;
    }
    f->unget = (unsigned char)c;
    f->flags &= ~LC_EOF;
    f->f._flags &= ~LC_FEOF;
    return (unsigned char)c;
}

LC_API size_t fread(void *p, size_t size, size_t n, FILE *fp) {
    lc_file_t *f = LC_F(fp);
    size_t want = size * n, got = 0;
    unsigned char *b = p;
    if (want == 0) {
        return 0;
    }
    if (f->unget >= 0) {
        b[got++] = (unsigned char)f->unget;
        f->unget = -1;
    }
    while (got < want) {
        if (f->rpos < f->rlen) {
            size_t k = f->rlen - f->rpos;
            if (k > want - got) {
                k = want - got;
            }
            memcpy(b + got, f->buf + f->rpos, k);
            f->rpos += k;
            got += k;
            continue;
        }
        if (to_read(f) != 0) {
            break;
        }
        if (want - got >= f->cap || f->mode == _IONBF) {
            // A big read goes straight to the caller's memory.
            ssize_t r = read(f->fd, b + got, want - got);
            if (r <= 0) {
                mark(f, r == 0 ? LC_EOF : LC_ERR);
                break;
            }
            got += (size_t)r;
            continue;
        }
        if (fill(f) != 0) {
            break;
        }
    }
    return got / size;
}

LC_API size_t fwrite(const void *p, size_t size, size_t n, FILE *fp) {
    if (size == 0 || n == 0) {
        return 0;
    }
    return lc_write(LC_F(fp), p, size * n) / size;
}

LC_API char *fgets(char *s, int n, FILE *fp) {
    lc_file_t *f = LC_F(fp);
    int i = 0;
    if (n <= 0) {
        return NULL;
    }
    while (i < n - 1) {
        int c = lc_getc(f);
        if (c == EOF) {
            break;
        }
        s[i++] = (char)c;
        if (c == '\n') {
            break;
        }
    }
    if (i == 0) {
        return NULL;
    }
    s[i] = '\0';
    return s;
}

LC_API int fputs(const char *s, FILE *fp) {
    size_t n = strlen(s);
    return lc_write(LC_F(fp), s, n) == n ? 0 : EOF;
}

LC_API int puts(const char *s) {
    size_t n = strlen(s);
    if (lc_write(&s_out, s, n) != n || lc_putc('\n', &s_out) == EOF) {
        return EOF;
    }
    return 0;
}

LC_API ssize_t getdelim(char **line, size_t *cap, int delim, FILE *fp) {
    lc_file_t *f = LC_F(fp);
    size_t n = 0;
    if (line == NULL || cap == NULL) {
        errno = EINVAL;
        return -1;
    }
    for (;;) {
        int c = lc_getc(f);
        if (c == EOF) {
            break;
        }
        if (n + 2 > *cap || *line == NULL) {
            size_t want = *cap < 64 ? 64 : *cap * 2;
            char *g = realloc(*line, want);
            if (g == NULL) {
                return -1;
            }
            *line = g;
            *cap = want;
        }
        (*line)[n++] = (char)c;
        if (c == delim) {
            break;
        }
    }
    if (n == 0) {
        return -1;
    }
    (*line)[n] = '\0';
    return (ssize_t)n;
}

LC_API ssize_t getline(char **line, size_t *cap, FILE *fp) {
    return getdelim(line, cap, '\n', fp);
}

// -- where a stream stands ---------------------------------------------------------------

LC_API int fseek(FILE *fp, long at, int whence) {
    lc_file_t *f = LC_F(fp);
    if (whence == SEEK_CUR) {
        at -= (long)(f->rlen - f->rpos) + (f->unget >= 0);
    }
    f->rpos = f->rlen = 0;
    f->unget = -1;
    if (f->wlen && lc_flush(f) != 0) {
        return -1;
    }
    if (lseek(f->fd, (off_t)at, whence) < 0) {
        return -1;
    }
    f->flags &= ~LC_EOF;
    f->f._flags &= ~LC_FEOF;
    return 0;
}

LC_API int fseeko(FILE *fp, off_t at, int whence) {
    return fseek(fp, (long)at, whence);
}

LC_API long ftell(FILE *fp) {
    lc_file_t *f = LC_F(fp);
    off_t at = lseek(f->fd, 0, SEEK_CUR);
    if (at < 0) {
        return -1;
    }
    return (long)at - (long)(f->rlen - f->rpos) - (f->unget >= 0) + (long)f->wlen;
}

LC_API off_t ftello(FILE *fp) {
    return (off_t)ftell(fp);
}

LC_API void rewind(FILE *fp) {
    fseek(fp, 0, SEEK_SET);
    clearerr(fp);
}

LC_API int feof(FILE *fp) {
    return (LC_F(fp)->flags & LC_EOF) != 0;
}

LC_API int ferror(FILE *fp) {
    return (LC_F(fp)->flags & LC_ERR) != 0;
}

LC_API void clearerr(FILE *fp) {
    LC_F(fp)->flags &= ~(LC_EOF | LC_ERR);
    fp->_flags &= ~(LC_FEOF | LC_FERR);
}

LC_API int fileno(FILE *fp) {
    return LC_F(fp)->fd;
}

LC_API void perror(const char *s) {
    int e = errno;
    if (s != NULL && *s) {
        fputs(s, &s_err.f);
        fputs(": ", &s_err.f);
    }
    fputs(strerror(e), &s_err.f);
    lc_putc('\n', &s_err);
}

LC_API int remove(const char *path) {
    if (unlink(path) == 0) {
        return 0;
    }
    return errno == EISDIR ? rmdir(path) : -1;
}

#if defined(LC_NEWLIB)
LC_EXPORT(__getdelim, getdelim);
LC_EXPORT(__getline, getline);
#endif

#if defined(LC_GLIBC)
// glibc's own names for calls a program's headers may route through.
LC_API int _IO_getc(FILE *fp) __attribute__((alias("getc")));
LC_API int _IO_putc(int c, FILE *fp) __attribute__((alias("putc")));
LC_API int fgetc_unlocked(FILE *fp) __attribute__((alias("getc")));
LC_API int fputc_unlocked(int c, FILE *fp) __attribute__((alias("putc")));
LC_API size_t fread_unlocked(void *p, size_t size, size_t n, FILE *fp)
    __attribute__((alias("fread")));
LC_API size_t fwrite_unlocked(const void *p, size_t size, size_t n, FILE *fp)
    __attribute__((alias("fwrite")));
LC_API char *fgets_unlocked(char *s, int n, FILE *fp) __attribute__((alias("fgets")));
LC_API int fputs_unlocked(const char *s, FILE *fp) __attribute__((alias("fputs")));
LC_API int fflush_unlocked(FILE *fp) __attribute__((alias("fflush")));
LC_API ssize_t __getdelim(char **line, size_t *cap, int delim, FILE *fp)
    __attribute__((alias("getdelim")));
#endif

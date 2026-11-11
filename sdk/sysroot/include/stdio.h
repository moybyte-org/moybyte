// The board's stdio.h, and the POSIX calls this newlib leaves undeclared,
// which the SDK's C library carries (sdk/libc/lc_stdio.c).
#include_next <stdio.h>

#ifndef _MOY_STDIO_H
#define _MOY_STDIO_H

#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

ssize_t getdelim(char **line, size_t *cap, int delim, FILE *fp);
ssize_t getline(char **line, size_t *cap, FILE *fp);

#ifdef __cplusplus
}
#endif

#endif

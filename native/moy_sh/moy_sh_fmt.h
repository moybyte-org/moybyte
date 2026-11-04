// moy_sh_fmt: the shell's vsnprintf and snprintf (moy_sh_fmt.c), which
// moy_sh_posix.h names for dash and the C built-ins: every integer conversion
// at every length, where a board's nano formatter has no 64-bit ones.

#ifndef MOY_SH_FMT_H
#define MOY_SH_FMT_H

#include <stdarg.h>
#include <stddef.h>

int moy_sh_vsnprintf(char *buf, size_t cap, const char *fmt, va_list ap);
int moy_sh_snprintf(char *buf, size_t cap, const char *fmt, ...);

#endif // MOY_SH_FMT_H

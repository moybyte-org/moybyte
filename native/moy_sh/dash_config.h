// dash_config.h: what dash's configure would decide, for every target the
// shell builds on (the host under the sanitizers, the boards' newlib, the
// unix port). Every dash source is compiled with `-include dash_config.h`, as
// dash's own build force-includes its config.h.

#ifndef MOY_SH_DASH_CONFIG_H
#define MOY_SH_DASH_CONFIG_H

#ifndef _GNU_SOURCE
#define _GNU_SOURCE 1
#endif
#undef _FORTIFY_SOURCE

#define BSD 1
#define SHELL 1
#define JOBS 1
#define SMALL 1                  // no line editor: the faces edit the line
#define WITH_LINENO 1

#define HAVE_BSEARCH 1
#define HAVE_DECL_ISBLANK 1
#define HAVE_FACCESSAT 1
#define HAVE_ISALPHA 1
#define HAVE_KILLPG 1
#define HAVE_MEMPCPY 1
#define HAVE_MEMRCHR 1
#define HAVE_STPCPY 1
#define HAVE_STRCHRNUL 1
#define HAVE_STRSIGNAL 1
#define HAVE_STRTOD 1
#define HAVE_STRTOIMAX 1
#define HAVE_STRTOUMAX 1
#define HAVE_SYSCONF 1
#define HAVE_TEE 1
#define HAVE_MEMFD_CREATE 1
#define HAVE_F_DUPFD_CLOEXEC 1
#define USE_TEE 0
#define USE_MEMFD_CREATE 0

#define _PATH_BSHELL "/bin/sh"
#define _PATH_DEVNULL "/dev/null"
#define _PATH_TTY "/dev/tty"

// <termios.h> brings the C library's tty defaults, whose CEOF is a control
// character; dash's syntax.h names a syntax class CEOF. Taken first and
// dropped, so the class is the only CEOF any source sees.
#include <termios.h>
#undef CEOF

#include "moy_sh_g.h"

#endif // MOY_SH_DASH_CONFIG_H

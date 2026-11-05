/* The C23 search functions a newer C library defines (glibc's since C23):
   bsearch, strchr, strrchr, strstr, strpbrk, strchrnul, memchr and memrchr
   answer a const pointer for a const argument. tests/test_moy_sh.py
   force-includes this into every shell source under -Werror, so a host with
   an older library refuses a dropped const as CI's compiler does. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE 1
#endif
#include <stdlib.h>
#include <string.h>

/* const void * for a pointer to const, void * otherwise: (void *)1 is not a
   null pointer constant, so the conditional takes both operands' qualifiers */
#define MOY_C23_Q(p) (1 ? (p) : (void *)1)
#define MOY_C23_STR(f, s, ...) \
    _Generic(MOY_C23_Q(s), const void *: (const char *)f((s), __VA_ARGS__), default: f((s), __VA_ARGS__))
#define MOY_C23_MEM(f, s, ...) \
    _Generic(MOY_C23_Q(s), const void *: (const void *)f((s), __VA_ARGS__), default: f((s), __VA_ARGS__))

/* A C23 glibc already defines these as const-keeping macros: replace them. */
#undef strchr
#define strchr(s, c) MOY_C23_STR(strchr, s, c)
#undef strrchr
#define strrchr(s, c) MOY_C23_STR(strrchr, s, c)
#undef strstr
#define strstr(s, n) MOY_C23_STR(strstr, s, n)
#undef strpbrk
#define strpbrk(s, a) MOY_C23_STR(strpbrk, s, a)
#undef strchrnul
#define strchrnul(s, c) MOY_C23_STR(strchrnul, s, c)
#undef memchr
#define memchr(s, c, n) MOY_C23_MEM(memchr, s, c, n)
#undef memrchr
#define memrchr(s, c, n) MOY_C23_MEM(memrchr, s, c, n)
#undef bsearch
#define bsearch(k, b, n, z, f) \
    _Generic(MOY_C23_Q(b), const void *: (const void *)bsearch((k), (b), (n), (z), (f)), \
             default: bsearch((k), (b), (n), (z), (f)))

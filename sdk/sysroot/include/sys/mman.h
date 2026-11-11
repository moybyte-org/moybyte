// Anonymous memory from the OS (the table's mmap and munmap rows), with
// Linux's values, which the host's own header has and the kernel reads on
// every tier: a private, read-write, zeroed mapping its process holds until
// it unmaps it or ends. Nothing maps a file.
#ifndef _MOY_SYS_MMAN_H
#define _MOY_SYS_MMAN_H

#include <stddef.h>
#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PROT_NONE 0x0
#define PROT_READ 0x1
#define PROT_WRITE 0x2
#define PROT_EXEC 0x4

#define MAP_SHARED 0x01
#define MAP_PRIVATE 0x02
#define MAP_ANONYMOUS 0x20
#define MAP_ANON MAP_ANONYMOUS

#define MAP_FAILED ((void *)-1)

void *mmap(void *addr, size_t len, int prot, int flags, int fd, off_t off);
int munmap(void *addr, size_t len);

#ifdef __cplusplus
}
#endif

#endif

// moy_sh_arena: a session's memory, one region holding ALL of its state.
//
// Every allocation dash makes (ckmalloc, the parse-tree stack's blocks, the
// variables, the functions) and the session's globals themselves come from
// one region the session owns, in PSRAM on a board. That is what makes a
// fork cheap and exact: the bytes in use are the whole state of the shell,
// so a child is run over them and they are put back when it exits
// (moy_sh_arena_save, moy_sh_arena_restore), every pointer in them still good
// because the region never moves.
//
// The allocator is first fit over a free list with boundary tags, carving
// from the region's top when nothing fits; a free block that reaches the top
// gives it back, so the bytes a snapshot copies (the region's base to its
// top) are the live state and its holes. Under AddressSanitizer every free
// byte is poisoned, so a read of freed shell memory is reported on the host.

#ifndef MOY_SH_ARENA_H
#define MOY_SH_ARENA_H

#include <stddef.h>

typedef struct moy_sh_arena moy_sh_arena_t;

// An arena over `n` bytes at `mem` (aligned to 16). NULL when n is too small.
moy_sh_arena_t *moy_sh_arena_init(void *mem, size_t n);

void *moy_sh_arena_alloc(moy_sh_arena_t *a, size_t n);
void *moy_sh_arena_realloc(moy_sh_arena_t *a, void *p, size_t n);
void moy_sh_arena_free(moy_sh_arena_t *a, void *p);

// The bytes a snapshot holds now: the region's base to its top.
size_t moy_sh_arena_used(const moy_sh_arena_t *a);
// The bytes free, holes and the top together.
size_t moy_sh_arena_free_bytes(const moy_sh_arena_t *a);

// Copy the state out (`moy_sh_arena_used` bytes into `to`), and back.
void moy_sh_arena_save(const moy_sh_arena_t *a, void *to);
void moy_sh_arena_restore(moy_sh_arena_t *a, const void *from, size_t n);

#endif // MOY_SH_ARENA_H

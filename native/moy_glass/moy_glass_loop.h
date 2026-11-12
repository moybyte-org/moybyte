// moy_glass_loop: the glass's stages of the kernel's frame (modmoy_glass.c,
// over the compositor moy_glass.loop_bind bound; moy_loop_board.c calls them).

#ifndef MOY_GLASS_LOOP_H
#define MOY_GLASS_LOOP_H

#include <stdbool.h>
#include <stdint.h>

void moy_glass_loop_present(void);
void moy_glass_loop_fence(void);
void moy_glass_loop_idle(void);
bool moy_glass_loop_light(int level);
// The frame's overlap figures into `v`, and which of them the compositor has.
bool moy_glass_loop_overlap(uint32_t v[7], uint8_t *has);
void moy_glass_loop_clear(void);

#endif // MOY_GLASS_LOOP_H

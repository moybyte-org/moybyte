#pragma once
#include <stdint.h>
void lcd_init(void);
/* frame: 320x200 palette indices (Doom's own raster); pal: 256 x BGRA words */
void lcd_blit_indexed(const uint8_t *frame, const uint32_t *pal);
#include <stdbool.h>
/* Re-tell the panel its mode: MADCTL + COLMOD, and with `full` the whole
 * register table, INVON and DISPON. The blit does this itself every frame. */
void lcd_reassert_mode(bool full);

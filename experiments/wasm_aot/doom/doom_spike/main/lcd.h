#pragma once
#include <stdint.h>
void lcd_init(void);
/* frame: 320x200 palette indices (Doom's own raster); pal: 256 x BGRA words */
void lcd_blit_indexed(const uint8_t *frame, const uint32_t *pal);

#pragma once
#include <stdint.h>
void lcd_init(void);
/* frame: 320x200 palette indices (Doom's own raster); pal: 256 x BGRA words */
void lcd_blit_indexed(const uint8_t *frame, const uint32_t *pal);
/* Panel state as the ST7789 reports it: RDDMADCTL 0x0B, RDDCOLMOD 0x0C,
 * RDDIM 0x0D (image mode: inversion). Expected 0x68 / 0x55 / 0x20-ish. */
void lcd_read_state(uint8_t *madctl, uint8_t *colmod, uint8_t *im);
/* Re-send the mode registers the picture depends on (MADCTL, COLMOD, INVON). */
void lcd_reassert_mode(void);

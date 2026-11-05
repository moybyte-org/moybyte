// moy_sd: the board's card, in C with no VM (docs/os_2026-11/system.md §2).
//
// The board names its card in its board.toml [hardware] table, whose pins
// reach the C as MOY_HW_<ROW>_<PIN> (tools/board_config.py's hw_defines), and
// moy_sd_card_up brings it up from those alone, so the kernel can reach the
// card on a boot no VM has run in (the recovery floor's shell) and the
// console's Python (`moy_sd.up()`) calls the same C:
//
//   sdmmc0 {clk, cmd, d0..d3}   an SDMMC card on slot 0 (the P4s), its LDO
//                               switched on first where mpconfigboard.h names
//                               the register (MOY_SD_MMC_LDO_REG, the P4s' LDO4)
//   spi2 {sd_cs, radio_cs}      an sdspi card ATTACHED to the panel's SPI2 (the
//                               T-Deck): the bus is esp_lcd's and is never
//                               initialised here, so the card comes up only
//                               after the panel, the radio's CS held high first
//   spi3 {sclk, mosi, miso, cs} an sdspi card on SPI3, whose bus is initialised
//                               here once (the Guition S3)
//
// The card is brought up once and never torn down (a teardown between ops is
// what hangs the T-Deck); only a failed bring-up gives back what it took. Its
// state is PSRAM: an idle card holds no internal SRAM of its own.

#ifndef MOY_SD_H
#define MOY_SD_H

#include <stdint.h>

// The board's card up: 0 and its sectors (a second call answers the card
// already up), else an esp_err_t with the step that failed in `*what`
// ("host_init", "card_init", ...). ESP_ERR_NOT_SUPPORTED where the board
// names no card.
int moy_sd_card_up(uint32_t *sectors, const char **what);

// The card's sectors, 0 while it is not up.
uint32_t moy_sd_card_sectors(void);

// `count` sectors moved from or to `buf`, raising nothing: ESP_OK or the
// driver's error. What native/moy_store's card volume reads through.
int moy_sd_card_io(uint32_t start, uint8_t *buf, uint32_t count, int write);

// What the card took given back (a failed mount's): the device, the sdspi
// driver or the SDMMC slot; never the panel's bus.
void moy_sd_card_release(void);

#endif // MOY_SD_H

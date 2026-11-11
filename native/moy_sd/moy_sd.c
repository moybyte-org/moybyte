// moy_sd: the board's card in C (moy_sd.h). modmoy_sd.c is its Python face.
//
// THE T-DECK shares ONE SPI host (SPI2) between the ST7789 panel and the
// microSD card. machine.SDCard hard-hangs the board when the panel is live
// because it calls spi_bus_initialize() again on a host esp_lcd already owns
// -- two driver stacks fighting over one peripheral. The ESP-IDF-documented
// fix ("Sharing the SPI Bus Among SD Cards and Other SPI Devices") is to
// initialize the bus ONCE and ATTACH each driver as a device: esp_lcd already
// ran spi_bus_initialize(), so here only sdspi_host_init() and
// sdspi_host_init_device() (which spi_bus_add_device's the card) run, and the
// card is probed. No bus re-init, no teardown of the panel -- SD reads AND
// writes work while the display runs, as long as nothing flushes the panel
// mid-transaction (the console's store sessions run between frames, and the
// recovery floor's shell between its draws).
//
// THE GUITION S3's card is the same sdspi card on a host of its own (SPI3):
// the bus is initialised here, once, and never torn down -- a machine.SDCard's
// finaliser frees its host at a VM stop, which the kernel's store must
// outlive. THE P4s' card is on SDMMC slot 0, powered by LDO4, likewise
// initialised once and kept: the host is shared with the C6's slot 1, so only
// the card's slot is ever deinitialised, and only by a failed bring-up.

#include <string.h>

#include "py/mpconfig.h"         // mpconfigboard.h: MOY_SD_MMC_LDO_REG
#include "moy_sd.h"

#if __has_include("esp_idf_version.h")
#include "esp_idf_version.h"
#endif

#ifdef ESP_IDF_VERSION
#include "esp_heap_caps.h"
#include "driver/gpio.h"
#include "driver/sdspi_host.h"
#include "driver/spi_master.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdmmc_cmd.h"
#include "esp_private/sdmmc_common.h"
#include "soc/soc_caps.h"
#if SOC_SDMMC_HOST_SUPPORTED
#include "driver/sdmmc_host.h"
#endif
#define MOY_SD_HAVE_IDF 1
#else
#define MOY_SD_HAVE_IDF 0
#endif

#define MOY_SD_KHZ 20000

// Which card the board has, from its board.toml [hardware] rows
// (tools/board_config.py's hw_defines): `sdmmc0` an SDMMC card on slot 0; an
// `sd_cs` on `spi2` a card attached to the panel's bus, the `radio_cs` beside
// it parked; `spi3` a card on a bus of its own.
#if defined(MOY_HW_SDMMC0_CLK)
#define SD_MMC 1
#elif defined(MOY_HW_SPI2_SD_CS)
#define SD_SPI_HOST SPI2_HOST
#define SD_SPI_CS MOY_HW_SPI2_SD_CS
#define SD_SPI_ATTACH 1
#if defined(MOY_HW_SPI2_RADIO_CS)
#define SD_PARK_CS MOY_HW_SPI2_RADIO_CS
#endif
#elif defined(MOY_HW_SPI3_CS)
#define SD_SPI_HOST SPI3_HOST
#define SD_SPI_CS MOY_HW_SPI3_CS
#define SD_SPI_SCK MOY_HW_SPI3_SCLK
#define SD_SPI_MOSI MOY_HW_SPI3_MOSI
#define SD_SPI_MISO MOY_HW_SPI3_MISO
#endif

#define MOY_SD_SECTOR 512
// Sectors per multi-block command, so a transfer holds a shared SPI bus for
// at most this many sectors at a time; on SDMMC also a 16 KB bounce in PSRAM,
// taken per call and halved until PSRAM can give it. The card holds no bounce
// between calls.
#define MOY_SD_RUN 32

#if MOY_SD_HAVE_IDF
static sdmmc_card_t *s_card = NULL;
static sdspi_dev_handle_t s_dev = -1;
static bool s_host_inited = false;
static int s_mmc_slot = -1;
// Where an SDMMC transfer's bounce comes from: PSRAM on cache-line bounds when
// the host reaches PSRAM (the P4's), else internal DMA memory.
static uint32_t s_bounce_caps = MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL;

void moy_sd_card_release(void) {
    if (s_card != NULL) {
        heap_caps_free(s_card);
        s_card = NULL;
    }
#if SOC_SDMMC_HOST_SUPPORTED
    if (s_mmc_slot >= 0) {
        sdmmc_host_deinit_slot(s_mmc_slot);
        s_mmc_slot = -1;
    }
#endif
    if (s_dev >= 0) {
        sdspi_host_remove_device(s_dev);
        s_dev = -1;
    }
    if (s_host_inited) {
        sdspi_host_deinit();
        s_host_inited = false;
    }
}

uint32_t moy_sd_card_sectors(void) {
    return s_card != NULL ? (uint32_t)s_card->csd.capacity : 0u;
}

// A failed step gives back what the bring-up took and names itself.
static int failed(esp_err_t err, const char *step, const char **what) {
    moy_sd_card_release();
    *what = step;
    return err;
}

// The card's state is no DMA buffer: PSRAM, so an idle card holds no internal
// SRAM of its own.
static sdmmc_card_t *card_state(void) {
    sdmmc_card_t *c = heap_caps_malloc(sizeof(sdmmc_card_t), MALLOC_CAP_SPIRAM);
    if (c == NULL) {
        c = heap_caps_malloc(sizeof(sdmmc_card_t), MALLOC_CAP_8BIT);
    }
    return c;
}

static int probe(sdmmc_host_t *host, const char **what) {
    s_card = card_state();
    if (s_card == NULL) {
        return failed(ESP_ERR_NO_MEM, "card_state", what);
    }
    esp_err_t err = sdmmc_card_init(host, s_card);
    return err != ESP_OK ? failed(err, "card_init", what) : ESP_OK;
}

#if defined(SD_SPI_HOST)
// The card as an sdspi device on the host's bus.
static int spi_up(const char **what) {
#if !defined(SD_SPI_ATTACH)
    spi_bus_config_t bus = {
        .sclk_io_num = SD_SPI_SCK,
        .mosi_io_num = SD_SPI_MOSI,
        .miso_io_num = SD_SPI_MISO,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = MOY_SD_RUN * MOY_SD_SECTOR,
    };
    // A bus initialised before (a bring-up that failed later) is kept.
    esp_err_t err = spi_bus_initialize(SD_SPI_HOST, &bus,
                                       SPI_DMA_CH_AUTO);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        *what = "bus";
        return err;
    }
#else
    esp_err_t err;
#endif
#if defined(SD_PARK_CS)
    gpio_set_level(SD_PARK_CS, 1);
    gpio_config_t park = {
        .pin_bit_mask = 1ULL << SD_PARK_CS,
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&park);
    gpio_set_level(SD_PARK_CS, 1);
#endif
    err = sdspi_host_init();
    if (err != ESP_OK) {
        return failed(err, "host_init", what);
    }
    s_host_inited = true;
    sdspi_device_config_t dev = SDSPI_DEVICE_CONFIG_DEFAULT();
    dev.host_id = SD_SPI_HOST;
    dev.gpio_cs = (gpio_num_t)SD_SPI_CS;
    err = sdspi_host_init_device(&dev, &s_dev);
    if (err != ESP_OK) {
        return failed(err, "init_device", what);
    }
    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = s_dev;
    host.max_freq_khz = MOY_SD_KHZ;
    return probe(&host, what);
}
#endif

#if defined(SD_MMC) && SOC_SDMMC_HOST_SUPPORTED
// The card on its SDMMC slot, its LDO switched on first: software-owned, tied
// to the 3.3V rail, then powered, and given 10 ms to settle before CMD0.
// Stock MicroPython never enables it, and without it the card times out
// whether one is there or not.
static int mmc_up(const char **what) {
#if defined(MOY_SD_MMC_LDO_REG)
    volatile uint32_t *ldo = (volatile uint32_t *)MOY_SD_MMC_LDO_REG;
    *ldo |= (1u << 7) | (1u << 14);
    *ldo |= 1u << 8;
    vTaskDelay(pdMS_TO_TICKS(10));
#endif
    sdmmc_slot_config_t cfg = SDMMC_SLOT_CONFIG_DEFAULT();
    cfg.width = 4;
    cfg.clk = (gpio_num_t)MOY_HW_SDMMC0_CLK;
    cfg.cmd = (gpio_num_t)MOY_HW_SDMMC0_CMD;
    cfg.d0 = (gpio_num_t)MOY_HW_SDMMC0_D0;
    cfg.d1 = (gpio_num_t)MOY_HW_SDMMC0_D1;
    cfg.d2 = (gpio_num_t)MOY_HW_SDMMC0_D2;
    cfg.d3 = (gpio_num_t)MOY_HW_SDMMC0_D3;
    // The host is initialised here or by whoever holds another slot
    // (ESP-Hosted's).
    esp_err_t err = sdmmc_host_init();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return failed(err, "mmc host_init", what);
    }
    err = sdmmc_host_init_slot(0, &cfg);
    if (err != ESP_OK) {
        return failed(err, "mmc slot", what);
    }
    s_mmc_slot = 0;
#if SOC_SDMMC_PSRAM_DMA_CAPABLE
    s_bounce_caps = MALLOC_CAP_SPIRAM | MALLOC_CAP_CACHE_ALIGNED;
#endif
    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.slot = 0;
    host.max_freq_khz = MOY_SD_KHZ;
    return probe(&host, what);
}
#endif

int moy_sd_card_up(uint32_t *sectors, const char **what) {
    *what = "";
    if (s_card == NULL) {
#if defined(SD_SPI_HOST)
        int err = spi_up(what);
#elif defined(SD_MMC) && SOC_SDMMC_HOST_SUPPORTED
        int err = mmc_up(what);
#else
        int err = ESP_ERR_NOT_SUPPORTED;
        *what = "no card";
#endif
        if (err != ESP_OK) {
            return err;
        }
    }
    *sectors = moy_sd_card_sectors();
    return ESP_OK;
}

// `count` sectors from or to `buf`, which may live anywhere (a PSRAM
// bytearray, unaligned), as multi-block commands of up to MOY_SD_RUN sectors:
// on a write the card's busy time is paid once per command instead of once
// per sector, and that wait is most of what a single-block write costs.
//
// On SPI the transfer allocates nothing. sdspi moves every block through its
// own DMA buffer (516 bytes, taken at the card's first data command, at mount)
// and copies to or from `buf` with the CPU, so `buf` goes to the card as it
// is, through sdmmc_{read,write}_sectors_dma: the public sdmmc_read_sectors
// would refuse a PSRAM `buf` and take a DMA block per call for it. Internal
// DMA memory is what WiFi and BLE run short of together: a buffer taken there
// per call is a store read that fails with EIO under a radio's burst.
//
// On SDMMC a run goes through a bounce the host's DMA reaches (aligned PSRAM
// on the P4), taken for this call and given back; when its memory cannot give
// it the run halves, down to one sector, and with not even that the call fails
// with ESP_ERR_NO_MEM.
int moy_sd_card_io(uint32_t start, uint8_t *buf, uint32_t count, int write) {
    if (s_card == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    if (count == 0) {
        return ESP_OK;
    }
    if (s_dev >= 0) {
        esp_err_t err = ESP_OK;
        for (uint32_t i = 0; i < count && err == ESP_OK; i += MOY_SD_RUN) {
            uint32_t n = count - i < MOY_SD_RUN ? count - i : MOY_SD_RUN;
            uint8_t *at = buf + (size_t)i * MOY_SD_SECTOR;
            size_t len = (size_t)n * MOY_SD_SECTOR;
            err = write ? sdmmc_write_sectors_dma(s_card, at, start + i, n, len)
                        : sdmmc_read_sectors_dma(s_card, at, start + i, n, len);
        }
        return err;
    }
    uint32_t run = count < MOY_SD_RUN ? count : MOY_SD_RUN;
    uint8_t *bounce = NULL;
    while (run > 0) {
        bounce = heap_caps_malloc((size_t)run * MOY_SD_SECTOR, s_bounce_caps);
        if (bounce != NULL) {
            break;
        }
        run /= 2;
    }
    if (bounce == NULL) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = ESP_OK;
    for (uint32_t i = 0; i < count && err == ESP_OK; i += run) {
        uint32_t n = count - i < run ? count - i : run;
        uint8_t *at = buf + (size_t)i * MOY_SD_SECTOR;
        if (write) {
            memcpy(bounce, at, (size_t)n * MOY_SD_SECTOR);
            err = sdmmc_write_sectors(s_card, bounce, start + i, n);
        } else {
            err = sdmmc_read_sectors(s_card, bounce, start + i, n);
            if (err == ESP_OK) {
                memcpy(at, bounce, (size_t)n * MOY_SD_SECTOR);
            }
        }
    }
    heap_caps_free(bounce);
    return err;
}

#endif // MOY_SD_HAVE_IDF

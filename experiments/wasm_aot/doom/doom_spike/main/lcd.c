/*
 * The T-Deck's ST7789 over SPI, brought up exactly as the console does it
 * (firmware/lilygo_t_deck_plus_mainline/native/moy_lcd/modmoy_lcd.c: same
 * pins, clock, MADCTL and register table), reduced to what a spike needs:
 * a 320x200 indexed frame in PSRAM goes out through the palette in bands of
 * 20 rows via two internal-SRAM DMA bounce buffers -- the panel DMA only
 * ever reads internal SRAM (the #66 SRAM-bounce design).
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_panel_ops.h"
#include "lcd.h"

#define PIN_SCK      40
#define PIN_MOSI     41
#define PIN_MISO     38
#define PIN_DC       11
#define PIN_CS       12
#define PIN_BL       42
#define PIN_POWERON  10
#define PIN_SD_CS    39
#define PIN_RADIO_CS 9

#define W          320
#define H          240
#define DOOM_H     200
#define Y0         ((H - DOOM_H) / 2)
#define BAND_ROWS  20
#define BAND_PX    (W * BAND_ROWS)
#define BAND_BYTES (BAND_PX * 2)

typedef struct {
    uint8_t cmd;
    uint8_t len;
    uint16_t delay_ms;
    uint8_t data[14];
} lcd_cmd_t;

static const lcd_cmd_t INIT[] = {
    { 0x13, 0, 10, { 0 } },                                    /* NORON */
    { 0xB6, 2, 0, { 0x0A, 0x82 } },
    { 0xB2, 5, 0, { 0x0C, 0x0C, 0x00, 0x33, 0x33 } },          /* PORCTRL */
    { 0xB7, 1, 0, { 0x35 } },                                  /* GCTRL */
    { 0xBB, 1, 0, { 0x28 } },                                  /* VCOMS */
    { 0xC0, 1, 0, { 0x0C } },                                  /* LCMCTRL */
    { 0xC2, 1, 0, { 0x01 } },                                  /* VDVVRHEN */
    { 0xC3, 1, 0, { 0x13 } },                                  /* VRHS */
    { 0xC4, 1, 0, { 0x20 } },                                  /* VDVSET */
    { 0xC6, 1, 0, { 0x0F } },                                  /* FRCTR2 */
    { 0xD0, 2, 0, { 0xA4, 0xA1 } },                            /* PWCTRL1 */
    { 0xE0, 14, 0, { 0xD0, 0x00, 0x02, 0x07, 0x0A, 0x28, 0x32,
                     0x44, 0x42, 0x06, 0x0E, 0x12, 0x14, 0x17 } },
    { 0xE1, 14, 0, { 0xD0, 0x00, 0x02, 0x07, 0x0A, 0x28, 0x31,
                     0x54, 0x47, 0x0E, 0x1C, 0x17, 0x1B, 0x1E } },
};

static esp_lcd_panel_io_handle_t s_io;
static esp_lcd_panel_handle_t s_panel;
static uint16_t *s_bounce[2];

static void park_pin(int gpio, int level)
{
    gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << gpio,
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&cfg);
    gpio_set_level(gpio, level);
}

void lcd_init(void)
{
    park_pin(PIN_POWERON, 1);
    park_pin(PIN_SD_CS, 1);
    park_pin(PIN_RADIO_CS, 1);
    park_pin(PIN_BL, 0);
    vTaskDelay(pdMS_TO_TICKS(50));

    spi_bus_config_t bus_cfg = {
        .sclk_io_num = PIN_SCK,
        .mosi_io_num = PIN_MOSI,
        .miso_io_num = PIN_MISO,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = BAND_BYTES + 64,
        .isr_cpu_id = ESP_INTR_CPU_AFFINITY_0,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus_cfg, SPI_DMA_CH_AUTO));

    esp_lcd_panel_io_spi_config_t io_cfg = {
        .cs_gpio_num = PIN_CS,
        .dc_gpio_num = PIN_DC,
        .spi_mode = 0,
        .pclk_hz = 80 * 1000 * 1000,
        .trans_queue_depth = 4,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST,
                                             &io_cfg, &s_io));
    esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = -1,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_BGR,
        .bits_per_pixel = 16,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(s_io, &panel_cfg, &s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(s_panel));
    vTaskDelay(pdMS_TO_TICKS(120));
    ESP_ERROR_CHECK(esp_lcd_panel_init(s_panel));
    vTaskDelay(pdMS_TO_TICKS(10));
    ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(s_panel, true));
    ESP_ERROR_CHECK(esp_lcd_panel_mirror(s_panel, true, false));
    for (size_t i = 0; i < sizeof(INIT) / sizeof(INIT[0]); i++) {
        const lcd_cmd_t *c = &INIT[i];
        ESP_ERROR_CHECK(esp_lcd_panel_io_tx_param(s_io, c->cmd, c->len ? c->data : NULL,
                                                  c->len));
        if (c->delay_ms) {
            vTaskDelay(pdMS_TO_TICKS(c->delay_ms));
        }
    }
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(s_panel, true));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(s_panel, true));
    vTaskDelay(pdMS_TO_TICKS(120));

    for (int i = 0; i < 2; i++) {
        s_bounce[i] = heap_caps_malloc(BAND_BYTES, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
        assert(s_bounce[i]);
    }
    memset(s_bounce[0], 0, BAND_BYTES);
    for (int y = 0; y < H; y += BAND_ROWS) {
        ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(s_panel, 0, y, W, y + BAND_ROWS,
                                                  s_bounce[0]));
    }
    gpio_set_level(PIN_BL, 1);
}

void lcd_blit_indexed(const uint8_t *frame, const uint32_t *pal)
{
    uint16_t lut[256];
    for (int i = 0; i < 256; i++) {
        uint32_t c = pal[i];                       /* b, g, r, a from the LSB */
        uint32_t b = c & 0xff, g = (c >> 8) & 0xff, r = (c >> 16) & 0xff;
        uint16_t v = (uint16_t)(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3));
        lut[i] = (uint16_t)((v << 8) | (v >> 8));  /* MSB first on the wire */
    }
    for (int k = 0; k < DOOM_H / BAND_ROWS; k++) {
        uint16_t *dst = s_bounce[k & 1];
        const uint8_t *src = frame + k * BAND_PX;
        for (int n = 0; n < BAND_PX; n += 4) {
            dst[n] = lut[src[n]];
            dst[n + 1] = lut[src[n + 1]];
            dst[n + 2] = lut[src[n + 2]];
            dst[n + 3] = lut[src[n + 3]];
        }
        int y = Y0 + k * BAND_ROWS;
        /* esp_lcd serialises: this call's CASET waits out the previous
         * band's colour DMA, so the other bounce buffer is free by now and
         * the conversion above overlapped that transfer. */
        esp_lcd_panel_draw_bitmap(s_panel, 0, y, W, y + BAND_ROWS, dst);
    }
}

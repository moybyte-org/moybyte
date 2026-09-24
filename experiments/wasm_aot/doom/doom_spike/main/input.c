/*
 * T-Deck input for the Doom spike: the trackball (four GPIOs pulse low when
 * rolled, GPIO0 is the click) and the C3 keyboard controller at I2C 0x55,
 * which reports one ASCII byte per PRESS and nothing on release or hold. So
 * every key here is a timed hold: a press puts the Doom key down and a
 * deadline releases it, and rolling the ball keeps extending its direction.
 */
#include "sdkconfig.h"
#if !CONFIG_IDF_TARGET_ESP32S3
#include "input.h"
void input_init(void) {}
void input_poll(void) {}
uint32_t input_pop_key(void) { return 0; }
#else
#include <ctype.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_timer.h"
#include "input.h"

#define KEY_RIGHTARROW 0xae
#define KEY_LEFTARROW  0xac
#define KEY_UPARROW    0xad
#define KEY_DOWNARROW  0xaf
#define KEY_STRAFE_L   0xa0
#define KEY_STRAFE_R   0xa1
#define KEY_USE        0xa2
#define KEY_FIRE       0xa3
#define KEY_ESCAPE     27
#define KEY_ENTER      13
#define KEY_BACKSPACE  0x7f

#define TB_UP    3
#define TB_DOWN  15
#define TB_LEFT  1
#define TB_RIGHT 2
#define TB_CLICK 0

#define QUEUE 64
static volatile uint16_t s_q[QUEUE];
static volatile unsigned s_qw, s_qr;

static volatile uint32_t s_pulses[4];
static const int s_tb_gpio[4] = { TB_UP, TB_DOWN, TB_LEFT, TB_RIGHT };
static const uint8_t s_tb_key[4] = { KEY_UPARROW, KEY_DOWNARROW, KEY_LEFTARROW, KEY_RIGHTARROW };

typedef struct {
    uint8_t key;
    int64_t release_at;
} held_t;
static held_t s_held[12];
static int s_click_down;
static int64_t s_next_kbd_us;
static i2c_master_dev_handle_t s_kbd;

static void post(int pressed, uint8_t key)
{
    unsigned w = s_qw;
    if (((w + 1) % QUEUE) == s_qr) {
        return;
    }
    s_q[w] = (uint16_t)((pressed << 8) | key);
    s_qw = (w + 1) % QUEUE;
}

uint32_t input_pop_key(void)
{
    if (s_qr == s_qw) {
        return 0;
    }
    uint16_t v = s_q[s_qr];
    s_qr = (s_qr + 1) % QUEUE;
    return v;
}

static void hold(uint8_t key, int ms)
{
    int64_t until = esp_timer_get_time() + (int64_t)ms * 1000;
    int free_slot = -1;
    for (int i = 0; i < 12; i++) {
        if (s_held[i].key == key) {
            s_held[i].release_at = until;
            return;
        }
        if (!s_held[i].key && free_slot < 0) {
            free_slot = i;
        }
    }
    if (free_slot < 0) {
        return;
    }
    s_held[free_slot].key = key;
    s_held[free_slot].release_at = until;
    post(1, key);
}

static void IRAM_ATTR tb_isr(void *arg)
{
    s_pulses[(int)arg]++;
}

static uint8_t map_ascii(uint8_t c)
{
    switch (c) {
        case 'w': return KEY_UPARROW;
        case 's': return KEY_DOWNARROW;
        case 'a': return KEY_LEFTARROW;
        case 'd': return KEY_RIGHTARROW;
        case 'z': return KEY_STRAFE_L;
        case 'x': return KEY_STRAFE_R;
        case ' ': return KEY_FIRE;
        case 'e': return KEY_USE;
        case '\r':
        case '\n': return KEY_ENTER;
        case 'q': return KEY_ESCAPE;
        case 0x08: return KEY_BACKSPACE;
        default: return (uint8_t)tolower(c);
    }
}

void input_init(void)
{
    gpio_config_t in = {
        .pin_bit_mask = (1ULL << TB_UP) | (1ULL << TB_DOWN) | (1ULL << TB_LEFT)
                        | (1ULL << TB_RIGHT),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .intr_type = GPIO_INTR_NEGEDGE,
    };
    gpio_config(&in);
    gpio_config_t click = {
        .pin_bit_mask = 1ULL << TB_CLICK,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&click);
    gpio_install_isr_service(0);
    for (int i = 0; i < 4; i++) {
        gpio_isr_handler_add(s_tb_gpio[i], tb_isr, (void *)i);
    }

    i2c_master_bus_config_t bus = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = 18,
        .scl_io_num = 8,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bh;
    if (i2c_new_master_bus(&bus, &bh) == ESP_OK) {
        i2c_device_config_t dev = {
            .dev_addr_length = I2C_ADDR_BIT_LEN_7,
            .device_address = 0x55,
            .scl_speed_hz = 400000,
        };
        if (i2c_master_bus_add_device(bh, &dev, &s_kbd) != ESP_OK) {
            s_kbd = NULL;
        }
    }
}

void input_poll(void)
{
    int64_t now = esp_timer_get_time();

    for (int i = 0; i < 4; i++) {
        uint32_t n = s_pulses[i];
        if (n) {
            s_pulses[i] = 0;
            hold(s_tb_key[i], 140);
        }
    }
    int click = gpio_get_level(TB_CLICK) == 0;
    if (click != s_click_down) {
        s_click_down = click;
        post(click, KEY_FIRE);
    }

    if (s_kbd && now >= s_next_kbd_us) {
        s_next_kbd_us = now + 30000;
        uint8_t c = 0;
        if (i2c_master_receive(s_kbd, &c, 1, 5) == ESP_OK && c) {
            uint8_t k = map_ascii(c);
            hold(k, (k >= 0xa0 && k <= 0xaf) ? 220 : 60);
        }
    }

    for (int i = 0; i < 12; i++) {
        if (s_held[i].key && now >= s_held[i].release_at) {
            post(0, s_held[i].key);
            s_held[i].key = 0;
        }
    }
}

#endif /* CONFIG_IDF_TARGET_ESP32S3 */

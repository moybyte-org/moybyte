/*
 * T-Deck input for the Doom spike: the trackball (four GPIOs pulse low when
 * rolled, GPIO0 is the click) and the C3 keyboard controller at I2C 0x55.
 *
 * The keyboard has two modes. Its default reports one ASCII byte per PRESS
 * and nothing on hold or release, so a game cannot know a key is still down;
 * command 0x03 switches it to streaming the raw key MATRIX (five bytes, one
 * bit per key, level state), which is what the console uses for hold-to-move
 * (device/moybyte/input.py, RAW_KEYS -- the table below is that one). Firmware
 * older than 2025-06-12 ignores 0x03 and keeps sending ASCII; on such a board
 * build with KBD_ASCII_ONLY and keys are timed holds. Rolling the ball keeps
 * extending its direction either way.
 */
#include "sdkconfig.h"
#if !CONFIG_IDF_TARGET_ESP32S3
#include "input.h"
void input_init(void) {}
void input_poll(void) {}
uint32_t input_pop_key(void) { return 0; }
#else
#include <ctype.h>
#include <stdio.h>
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
static int s_raw;                 /* the matrix streams (0x03 accepted) */
volatile int g_dump_request;      /* 'o' pressed: main.c dumps the next frame */
static uint64_t s_matrix_down;    /* Doom keys down per the last matrix read */

/* the vendor matrix: byte index, bit -> ASCII (device/moybyte/input.py) */
typedef struct {
    uint8_t byte, bit, ascii;
} matrix_key_t;
static const matrix_key_t MATRIX[] = {
    { 0, 0x01, 'q' }, { 0, 0x02, 'w' }, { 0, 0x08, 'a' }, { 0, 0x20, ' ' },
    { 1, 0x01, 'e' }, { 1, 0x02, 's' }, { 1, 0x04, 'd' }, { 1, 0x08, 'p' },
    { 1, 0x10, 'x' }, { 1, 0x20, 'z' },
    { 2, 0x01, 'r' }, { 2, 0x02, 'g' }, { 2, 0x04, 't' }, { 2, 0x10, 'v' },
    { 2, 0x20, 'c' }, { 2, 0x40, 'f' },
    { 3, 0x01, 'u' }, { 3, 0x02, 'h' }, { 3, 0x04, 'y' }, { 3, 0x08, '\r' },
    { 3, 0x10, 'b' }, { 3, 0x20, 'n' }, { 3, 0x40, 'j' },
    { 4, 0x01, 'o' }, { 4, 0x02, 'l' }, { 4, 0x04, 'i' }, { 4, 0x08, 0x08 },
    { 4, 0x20, 'm' }, { 4, 0x40, 'k' },
};
#define MATRIX_N (sizeof(MATRIX) / sizeof(MATRIX[0]))

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

/* The console's scheme: W A S D steer, L (right thumb, home row) and SPACE
 * fire, K uses; ENTER confirms, BACKSPACE is the menu key. Z/X strafe. */
static uint8_t map_ascii(uint8_t c)
{
    switch (c) {
        case 'w': return KEY_UPARROW;
        case 's': return KEY_DOWNARROW;
        case 'a': return KEY_LEFTARROW;
        case 'd': return KEY_RIGHTARROW;
        case 'z': return KEY_STRAFE_L;
        case 'x': return KEY_STRAFE_R;
        case ' ':
        case 'l': return KEY_FIRE;
        case 'k': return KEY_USE;
        case '\r':
        case '\n': return KEY_ENTER;
        case 0x08: return KEY_ESCAPE;
        default: return (uint8_t)tolower(c);
    }
}

/* GHOSTING. The matrix has no diodes, so three held keys on three corners of
 * a rectangle read the fourth corner as pressed too -- and W + A + L (forward,
 * turn, fire on the home row) put that phantom on BACKSPACE, the menu key:
 * the menu opened by itself mid-fight and the game paused (2026-09-25). The
 * matrix cannot tell W+A+L from W+A+Backspace, so a press that completes a
 * rectangle with three keys already down is ambiguous, and the tie goes to
 * the key a player is likelier to mean: fire/use/move over letters over the
 * menu key. Wrong at worst costs a menu keypress; right keeps the fight. */
static int key_priority(uint8_t ascii)
{
    switch (map_ascii(ascii)) {
        case KEY_UPARROW: case KEY_DOWNARROW: case KEY_LEFTARROW: case KEY_RIGHTARROW:
        case KEY_FIRE: case KEY_USE: case KEY_STRAFE_L: case KEY_STRAFE_R:
            return 3;
        case KEY_ENTER:
            return 2;
        case KEY_ESCAPE:
            return 0;
        default:
            return 1;
    }
}

static int completes_rectangle(unsigned i, uint64_t down)
{
    uint8_t c = MATRIX[i].byte, r = MATRIX[i].bit;
    for (unsigned j = 0; j < MATRIX_N; j++) {          /* same column, other row */
        if (j == i || !((down >> j) & 1) || MATRIX[j].byte != c) {
            continue;
        }
        for (unsigned k = 0; k < MATRIX_N; k++) {      /* same row, other column */
            if (k == i || !((down >> k) & 1) || MATRIX[k].bit != r || MATRIX[k].byte == c) {
                continue;
            }
            for (unsigned m = 0; m < MATRIX_N; m++) {  /* the opposite corner */
                if (((down >> m) & 1) && MATRIX[m].byte == MATRIX[k].byte
                    && MATRIX[m].bit == MATRIX[j].bit) {
                    return 1;
                }
            }
        }
    }
    return 0;
}

static uint64_t s_reported;   /* keys posted as down (a suppressed ghost never is) */

/* Raw matrix: five level bytes -> Doom key edges against the last read. */
static void matrix_poll(const uint8_t *d)
{
    uint64_t now_down = 0;
    for (unsigned i = 0; i < MATRIX_N; i++) {
        if (d[MATRIX[i].byte] & MATRIX[i].bit) {
            now_down |= 1ULL << i;
        }
    }
    /* releases first: anything reported down that the matrix no longer shows */
    for (unsigned i = 0; i < MATRIX_N; i++) {
        if (((s_reported >> i) & 1) && !((now_down >> i) & 1)) {
            s_reported &= ~(1ULL << i);
            post(0, map_ascii(MATRIX[i].ascii));
        }
    }
    /* presses: new keys, minus the losers of any rectangle they complete */
    uint64_t fresh = now_down & ~s_matrix_down & ~s_reported;
    int best = -1, best_pri = -1;
    for (unsigned i = 0; i < MATRIX_N; i++) {
        if (((fresh >> i) & 1) && completes_rectangle(i, now_down)) {
            int pri = key_priority(MATRIX[i].ascii);
            if (pri > best_pri) {
                best_pri = pri;
                best = (int)i;
            }
        }
    }
    for (unsigned i = 0; i < MATRIX_N; i++) {
        if (!((fresh >> i) & 1)) {
            continue;
        }
        if (completes_rectangle(i, now_down) && (int)i != best) {
            continue;                       /* the ghost corner: never reported */
        }
        if (MATRIX[i].ascii == 'o') {
            g_dump_request = 1;             /* diagnostics: dump this frame */
        }
        s_reported |= 1ULL << i;
        post(1, map_ascii(MATRIX[i].ascii));
    }
    s_matrix_down = now_down;
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
#ifndef KBD_ASCII_ONLY
    if (s_kbd) {
        uint8_t raw_cmd = 0x03;
        s_raw = i2c_master_transmit(s_kbd, &raw_cmd, 1, 20) == ESP_OK;
    }
#endif
    printf("INPUT keyboard=%s raw_mode=%d\n", s_kbd ? "found" : "absent", s_raw);
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
        if (s_raw) {
            /* No runtime "this looks like ASCII" fallback: a matrix frame with
             * two keys down in column 0 (W + Space = 0x22) is indistinguishable
             * from a printable byte, and taking it for one switched this
             * driver to single-byte reads while the keyboard kept streaming
             * the matrix -- every matrix byte then read as an ASCII code, and
             * A's byte, 0x08, is Backspace, the menu key (2026-09-25). The
             * mode is decided once at start-up (did 0x03 get an ACK) and the
             * firmware that ignores it is a build-time concern, not a guess
             * made mid-fight. */
            uint8_t d[5] = { 0 };
            if (i2c_master_receive(s_kbd, d, 5, 5) == ESP_OK) {
                matrix_poll(d);
            }
        }
        else {
            uint8_t c = 0;
            if (i2c_master_receive(s_kbd, &c, 1, 5) == ESP_OK && c) {
                if (c == 'o') {
                    g_dump_request = 1;
                }
                uint8_t k = map_ascii(c);
                hold(k, (k >= 0xa0 && k <= 0xaf) ? 220 : 60);
            }
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

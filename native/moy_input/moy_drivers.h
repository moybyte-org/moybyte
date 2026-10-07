// moy_input's drivers: the T-Deck keyboard and the touch controllers, each
// writing the kernel's input table from the input task, never a Python object
// (docs/kernel_survival_2026-10.md section 4.2).
//
// A driver's protocol is pure C over moy_i2c_t, so the host runs it against a
// fake bus (runtime/moy_input.py's driver bindings, tests/test_tdeck_input.py)
// and a board runs it over the kernel's bus (moy_input_task.c). Pins,
// interrupts and the task itself are the board half, in moy_input_task.c.
//
// TWO SIDES. A driver's PASS runs on the input task: one bus transaction set,
// staged into the driver under its lock (the keyboard writes its source's
// latch directly). Its FRAME side runs on the VM's task once a frame: the touch
// takes what the passes staged and runs the no-news contract (moy_touch.h).

#ifndef MOY_DRIVERS_H
#define MOY_DRIVERS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "moy_input.h"
#include "moy_touch.h"

#ifdef MOY_INPUT_BOARD
#include "freertos/FreeRTOS.h"
typedef portMUX_TYPE moy_drv_lock_t;
#define MOY_DRV_LOCK_INIT(l) portMUX_INITIALIZE(l)
#define MOY_DRV_LOCK(l) portENTER_CRITICAL_SAFE(l)
#define MOY_DRV_UNLOCK(l) portEXIT_CRITICAL_SAFE(l)
#else
typedef int moy_drv_lock_t;
#define MOY_DRV_LOCK_INIT(l) ((void)(l))
#define MOY_DRV_LOCK(l) ((void)(l))
#define MOY_DRV_UNLOCK(l) ((void)(l))
#endif

// The bus as a driver sees it: transfers to a 7-bit address, 0 on success.
typedef struct {
    int (*write)(void *ctx, uint8_t addr, const uint8_t *src, size_t n);
    int (*read)(void *ctx, uint8_t addr, uint8_t *dst, size_t n);
    int (*write_read)(void *ctx, uint8_t addr, const uint8_t *src, size_t ns,
                      uint8_t *dst, size_t nd);
    uint32_t (*ms)(void *ctx);          // on the VM's tick clock
    uint32_t (*us)(void *ctx);
    void (*sleep_ms)(void *ctx, uint32_t ms);
    void *ctx;
} moy_i2c_t;

// -- the T-Deck keyboard: the ESP32-C3 at 0x55 ----------------------------------
//
// ASCII mode: one byte per press edge (shift and sym resolved on the keyboard),
// each a one-shot key, the button it aliases held MOY_KBD_HOLD_MS. Raw mode:
// five bytes of matrix, level state, while a game runs (firmware of 2025-06-12
// or later; an older keyboard answers 0x03 with a stray ASCII byte and stays on
// the ASCII path for the session). The mode flip is queued and applied by the
// pass, which owns the bus: 0x03 into a game, 0x04 and a drain out of it.

#define MOY_KBD_ADDR 0x55
#define MOY_KBD_HOLD_MS 260
#define MOY_KBD_ERR_RUN 10      // consecutive failed reads that mean "gone"
#define MOY_KBD_DRAIN 4

typedef struct {
    moy_input_t *table;
    uint32_t src;
    bool available;
    bool raw_mode;
    bool raw_unsupported;
    bool raw_game;              // raw mode is wanted for games at all
    volatile int8_t want;       // a queued mode: -1 none, 0 ASCII, 1 game
    uint8_t err_run;
    uint32_t held;              // the ASCII latch's buttons
    uint32_t held_until;
    uint32_t raw_held;          // the matrix's last decode
    int32_t raw_key;
    uint8_t raw_bytes[5];
    bool raw_valid;
    uint32_t stat_n, stat_max_us, stat_over5, stat_over20, stat_timeouts;
    bool stat_max_raw;
} moy_kbd_t;

// The buttons a typed byte fires (an upper-case letter fires its lower case's).
uint32_t moy_kbd_buttons_for_key(int32_t key);
// Five matrix bytes -> the held buttons and the key (the last match wins).
void moy_kbd_decode_raw(const uint8_t d[5], uint32_t *held, int32_t *key);
// Probe the keyboard; it comes up in ASCII mode.
void moy_kbd_init(moy_kbd_t *k, const moy_i2c_t *bus, moy_input_t *table, uint32_t src);
void moy_kbd_game_mode(moy_kbd_t *k, bool on);
void moy_kbd_pass(moy_kbd_t *k, const moy_i2c_t *bus);
// For the host's binding: the struct's size, and the matrix table by entry.
size_t moy_kbd_sizeof(void);
int moy_kbd_raw_key(size_t i, uint8_t *byte, uint8_t *bit, uint8_t *key);

// -- touch controllers -----------------------------------------------------------

enum {
    MOY_TOUCH_GT911 = 1,        // status 0x814E, point 0x8150, 16-bit registers
    MOY_TOUCH_GSL3680 = 2,      // RAM-loaded; 8 bytes at 0x80, every read current
    MOY_TOUCH_AXS = 3,          // the AXS15231B bridge: a command, 8 bytes back
};

enum {
    MOY_RAW_NONE = 0,           // no news: the controller had no fresh sample
    MOY_RAW_UP = 1,             // a fresh sample with no finger
    MOY_RAW_POINT = 2,
};

#define MOY_TOUCH_SAFETY_MS 250  // a gated GT911 still reads at ~4 Hz
#define MOY_TOUCH_REPROBE 120    // passes between probes of a controller that was not there

typedef struct {
    uint8_t kind;
    uint8_t addr;
    bool available;
    bool yx;                    // a GT911 that lays its point out y then x (the T-Deck's)
    bool clear_first;           // clear the GT911's status before reading the point
    // the GT911's INT gate, counted by the board's ISR
    bool gate;
    volatile uint32_t int_count;
    uint32_t int_last;
    bool int_seen;
    bool touching;
    uint32_t last_read_ms;
    // the GSL3680's firmware, uploaded by the first passes
    const uint8_t *fw;
    size_t fw_len;
    uint32_t loaded;
    int8_t init_state;          // 0 idle, 1 upload wanted, 2 running, -1 failed
    uint16_t reprobe;
    // staged by the passes, taken by the frame
    moy_drv_lock_t lock;
    bool st_point;
    bool st_up;
    int32_t st_x, st_y;
    uint8_t st_fingers;
    // the frame side
    moy_touch_map_t map;
    moy_touch_held_t held;
    int32_t raw_x, raw_y;
    bool has_raw;
    uint8_t fingers;
    // the pass's latency, per transaction set
    uint32_t stat_n, stat_max_us, stat_over5, stat_over20, stat_skipped;
    bool fb_set;                // the first stall of 200 ms or more
    uint8_t fb_phase;           // 0 status, 1 point, 2 clear
    int16_t fb_status;          // -1: the status read itself failed
    uint32_t fb_ms, fb_n;
} moy_touchdev_t;

void moy_touchdev_init(moy_touchdev_t *d, uint8_t kind, uint8_t addr, const moy_touch_map_t *map,
                       bool extrapolate, float damp, int32_t hold_ms);
// Probe the controller (a GT911 also finds which of its two addresses answers;
// a GSL3680 is uploaded by the passes that follow, not here).
void moy_touchdev_probe(moy_touchdev_t *d, const moy_i2c_t *bus);
// The GSL3680's bring-up: reset, upload, start, check. True when it runs.
// `reset(ctx, int_low)` pulses the reset with INT held low and releases INT.
bool moy_gsl_init(moy_touchdev_t *d, const moy_i2c_t *bus,
                  void (*reset)(void *ctx, bool before), void *reset_ctx);
// One pass: the INT gate, one read, the result staged.
void moy_touchdev_pass(moy_touchdev_t *d, const moy_i2c_t *bus);
// The frame: what the passes staged, through the no-news contract.
void moy_touchdev_poll(moy_touchdev_t *d, uint32_t now, moy_touch_pt_t *out);
size_t moy_touchdev_sizeof(void);

// -- the trackball ---------------------------------------------------------------

typedef struct {
    volatile uint32_t counts[4];        // up, down, left, right pulses, from the ISRs
} moy_ball_t;

// The pulses since the last take, into out[4] (up, down, left, right).
void moy_ball_take(moy_ball_t *b, uint32_t out[4]);

// -- the board half (moy_input_task.c) -------------------------------------------

// The board's drivers on the input task, which a frame's kick runs one pass
// of. Each returns false where the board has no such device.
bool moy_input_board_kbd(moy_kbd_t **k);
bool moy_input_board_touch(moy_touchdev_t **d, int32_t w, int32_t h);
bool moy_input_board_ball(moy_ball_t **b, bool *click);
void moy_input_board_kick(void);
// The input task's stack never used so far, in bytes; 0 where there is no task.
uint32_t moy_input_board_stack_free(void);
// The pins the kernel's ISRs hold, which the VM's soft reset must leave alone.
bool moy_input_board_owns_pin(int gpio);

#endif // MOY_DRIVERS_H

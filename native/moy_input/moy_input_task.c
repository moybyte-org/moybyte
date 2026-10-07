// moy_input's board half: the bus adapter, the pins and ISRs, and the input
// task. moy_drivers.h has the drivers; this file is what a board's
// mpconfigboard.h selects them with:
//
//   MOY_INPUT_KBD_TDECK            the T-Deck's C3 keyboard on the kernel's bus
//   MOY_INPUT_BALL_UP/_DOWN/_LEFT/_RIGHT, MOY_INPUT_BALL_CLICK
//                                  the trackball's pulse pins and its click
//   MOY_INPUT_TOUCH_KIND           MOY_TOUCH_GT911, _GSL3680 or _AXS, and its
//     _ADDR, _YX, _CLEAR_FIRST, _INT (the GT911's gate, or the GSL's address
//     strap), _RST, _SWAP, _FLIP_X, _FLIP_Y, _RAW_W, _RAW_H, _RAW_X0, _RAW_Y0,
//     _EXTRAPOLATE, _HOLD_MS, _FW and _FW_LEN (the GSL3680's firmware)
//   MOY_INPUT_TASK                 the drivers' passes run on a core-0 task of
//                                  MOY_INPUT_TASK_STACK bytes, one per kick;
//                                  without it a kick runs the pass inline
//
// The task exists for the T-Deck (#69): its keyboard clock-stretches for tens
// of milliseconds on a bus it shares with the GT911, and a stall must block
// this task, never a frame. A board without it pays no stack.
//
// The ISRs are IRAM and touch only their counters, which are internal RAM.
// The VM's soft reset sweeps only the pins a Python handler holds
// (moy_kernel.c), so these survive it, and so does the task.

#include "py/mpconfig.h"

#include "moy_drivers.h"

#if defined(MOY_INPUT_BOARD) && (defined(MOY_INPUT_KBD_TDECK) || defined(MOY_INPUT_TOUCH_KIND) \
    || defined(MOY_INPUT_BALL_UP))

#include <string.h>

#include "driver/gpio.h"
#include "esp_attr.h"
#include "esp_rom_sys.h"
#include "esp_task.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/task.h"

#include "moy_bus.h"

// -- the bus -----------------------------------------------------------------------

#define BUS_TIMEOUT_MS 50

static uint8_t dev_of(uint8_t addr, uint8_t *dev) {
    return moy_bus_add(addr, BUS_TIMEOUT_MS, dev) == MOY_BUS_OK;
}

static int bus_write(void *ctx, uint8_t addr, const uint8_t *src, size_t n) {
    (void)ctx;
    uint8_t d;
    return dev_of(addr, &d) && moy_bus_write(d, src, n) == MOY_BUS_OK ? 0 : -1;
}

static int bus_read(void *ctx, uint8_t addr, uint8_t *dst, size_t n) {
    (void)ctx;
    uint8_t d;
    return dev_of(addr, &d) && moy_bus_read(d, dst, n) == MOY_BUS_OK ? 0 : -1;
}

static int bus_write_read(void *ctx, uint8_t addr, const uint8_t *src, size_t ns, uint8_t *dst,
                          size_t nd) {
    (void)ctx;
    uint8_t d;
    return dev_of(addr, &d) && moy_bus_write_read(d, src, ns, dst, nd) == MOY_BUS_OK ? 0 : -1;
}

static uint32_t bus_ms(void *ctx) {
    (void)ctx;
    return (uint32_t)(esp_timer_get_time() / 1000) & (MOY_INPUT_TICKS_PERIOD - 1u);
}

static uint32_t bus_us(void *ctx) {
    (void)ctx;
    return (uint32_t)esp_timer_get_time();
}

static void bus_sleep(void *ctx, uint32_t ms) {
    (void)ctx;
    vTaskDelay(pdMS_TO_TICKS(ms ? ms : 1));
}

static const moy_i2c_t s_bus = {
    bus_write, bus_read, bus_write_read, bus_ms, bus_us, bus_sleep, NULL,
};

// -- the devices -------------------------------------------------------------------

#ifdef MOY_INPUT_KBD_TDECK
static moy_kbd_t s_kbd;
static bool s_kbd_up;
#endif

#ifdef MOY_INPUT_TOUCH_KIND
static moy_touchdev_t s_touch;
static bool s_touch_up;
#ifdef MOY_INPUT_TOUCH_FW
extern const uint8_t MOY_INPUT_TOUCH_FW[];
extern const size_t MOY_INPUT_TOUCH_FW_LEN;
#endif
#endif

#ifdef MOY_INPUT_BALL_UP
static moy_ball_t s_ball;
static bool s_ball_up;
static const int8_t BALL_PINS[4] = {
    MOY_INPUT_BALL_UP, MOY_INPUT_BALL_DOWN, MOY_INPUT_BALL_LEFT, MOY_INPUT_BALL_RIGHT,
};

static void IRAM_ATTR ball_isr(void *arg) {
    s_ball.counts[(uintptr_t)arg]++;
}
#endif

#if defined(MOY_INPUT_TOUCH_KIND) && defined(MOY_INPUT_TOUCH_INT) && !defined(MOY_INPUT_TOUCH_FW)
static void IRAM_ATTR touch_int_isr(void *arg) {
    (void)arg;
    s_touch.int_count++;
}
#define TOUCH_INT_GATE 1
#endif

static bool s_isr_service;

static bool isr_service(void) {
    if (!s_isr_service) {
        esp_err_t e = gpio_install_isr_service(0);
        s_isr_service = e == ESP_OK || e == ESP_ERR_INVALID_STATE;
    }
    return s_isr_service;
}

bool moy_input_board_owns_pin(int gpio) {
    #ifdef MOY_INPUT_BALL_UP
    if (s_ball_up) {
        for (int i = 0; i < 4; i++) {
            if (BALL_PINS[i] == gpio) {
                return true;
            }
        }
    }
    #endif
    #ifdef TOUCH_INT_GATE
    if (s_touch.gate && gpio == MOY_INPUT_TOUCH_INT) {
        return true;
    }
    #endif
    (void)gpio;
    return false;
}

// -- the task ----------------------------------------------------------------------

static void one_pass(void) {
    #ifdef MOY_INPUT_KBD_TDECK
    if (s_kbd_up) {
        moy_kbd_pass(&s_kbd, &s_bus);
    }
    #endif
    #ifdef MOY_INPUT_TOUCH_KIND
    if (s_touch_up) {
        moy_touchdev_pass(&s_touch, &s_bus);
    }
    #endif
}

#ifdef MOY_INPUT_TASK
#ifndef MOY_INPUT_TASK_STACK
#define MOY_INPUT_TASK_STACK 3072
#endif
static TaskHandle_t s_task;

static void input_task(void *arg) {
    (void)arg;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        one_pass();
    }
}

static void task_up(void) {
    if (s_task == NULL) {
        xTaskCreatePinnedToCore(input_task, "moy_input", MOY_INPUT_TASK_STACK, NULL,
                                ESP_TASK_PRIO_MIN + 2, &s_task, 0);
    }
}

uint32_t moy_input_board_stack_free(void) {
    return s_task ? (uint32_t)uxTaskGetStackHighWaterMark(s_task) : 0;
}
#else
static void task_up(void) {
}

uint32_t moy_input_board_stack_free(void) {
    return 0;
}
#endif

void moy_input_board_kick(void) {
    #ifdef MOY_INPUT_TASK
    if (s_task != NULL) {
        xTaskNotifyGive(s_task);
    }
    #else
    one_pass();
    #endif
}

// -- bring-up ----------------------------------------------------------------------

bool moy_input_board_kbd(moy_kbd_t **k) {
    #ifdef MOY_INPUT_KBD_TDECK
    if (!s_kbd_up) {
        moy_input_t *t = moy_input_kernel();
        uint32_t h;
        if (t == NULL || moy_input_source(t, "kbd", 0, &h) != MOY_INPUT_OK) {
            return false;
        }
        moy_kbd_init(&s_kbd, &s_bus, t, h);
        s_kbd_up = true;
        task_up();
    }
    *k = &s_kbd;
    return true;
    #else
    (void)k;
    return false;
    #endif
}

#if defined(MOY_INPUT_TOUCH_KIND) && defined(MOY_INPUT_TOUCH_FW)
// The GSL3680's reset: INT low across the pulse picks address 0x40, and INT is
// released to an input with its pull-up once the firmware runs.
static void gsl_reset(void *ctx, bool before) {
    (void)ctx;
    if (before) {
        gpio_reset_pin(MOY_INPUT_TOUCH_INT);
        gpio_set_direction(MOY_INPUT_TOUCH_INT, GPIO_MODE_OUTPUT);
        gpio_set_level(MOY_INPUT_TOUCH_INT, 0);
        gpio_set_level(MOY_INPUT_TOUCH_RST, 0);
        vTaskDelay(pdMS_TO_TICKS(20));
        gpio_set_level(MOY_INPUT_TOUCH_RST, 1);
        vTaskDelay(pdMS_TO_TICKS(20));
    } else {
        gpio_set_direction(MOY_INPUT_TOUCH_INT, GPIO_MODE_INPUT);
        gpio_set_pull_mode(MOY_INPUT_TOUCH_INT, GPIO_PULLUP_ONLY);
    }
}
#endif

#ifndef MOY_INPUT_TOUCH_ADDR
#define MOY_INPUT_TOUCH_ADDR 0
#endif
#ifndef MOY_INPUT_TOUCH_HOLD_MS
#define MOY_INPUT_TOUCH_HOLD_MS MOY_TOUCH_HOLD_MS
#endif
#ifndef MOY_INPUT_TOUCH_EXTRAPOLATE
#define MOY_INPUT_TOUCH_EXTRAPOLATE 0
#endif
#ifndef MOY_INPUT_TOUCH_RAW_W
#define MOY_INPUT_TOUCH_RAW_W 0
#define MOY_INPUT_TOUCH_RAW_H 0
#endif
#ifndef MOY_INPUT_TOUCH_RAW_X0
#define MOY_INPUT_TOUCH_RAW_X0 0
#define MOY_INPUT_TOUCH_RAW_Y0 0
#endif

bool moy_input_board_touch(moy_touchdev_t **d, int32_t w, int32_t h) {
    #ifdef MOY_INPUT_TOUCH_KIND
    if (!s_touch_up) {
        moy_touch_map_t m = {
            .w = w, .h = h,
            .raw_w = MOY_INPUT_TOUCH_RAW_W, .raw_h = MOY_INPUT_TOUCH_RAW_H,
            .raw_x0 = MOY_INPUT_TOUCH_RAW_X0, .raw_y0 = MOY_INPUT_TOUCH_RAW_Y0,
            .swap = MOY_INPUT_TOUCH_SWAP, .flip_x = MOY_INPUT_TOUCH_FLIP_X,
            .flip_y = MOY_INPUT_TOUCH_FLIP_Y,
        };
        moy_touchdev_init(&s_touch, MOY_INPUT_TOUCH_KIND, MOY_INPUT_TOUCH_ADDR, &m,
                          MOY_INPUT_TOUCH_EXTRAPOLATE, 0.5f, MOY_INPUT_TOUCH_HOLD_MS);
        #ifdef MOY_INPUT_TOUCH_YX
        s_touch.yx = true;
        #endif
        #ifdef MOY_INPUT_TOUCH_CLEAR_FIRST
        s_touch.clear_first = true;
        #endif
        #ifdef MOY_INPUT_TOUCH_FW
        s_touch.fw = MOY_INPUT_TOUCH_FW;
        s_touch.fw_len = MOY_INPUT_TOUCH_FW_LEN;
        gpio_reset_pin(MOY_INPUT_TOUCH_RST);
        gpio_set_direction(MOY_INPUT_TOUCH_RST, GPIO_MODE_OUTPUT);
        gpio_set_level(MOY_INPUT_TOUCH_RST, 1);
        #endif
        moy_touchdev_probe(&s_touch, &s_bus);
        #ifdef MOY_INPUT_TOUCH_FW
        moy_gsl_init(&s_touch, &s_bus, gsl_reset, NULL);
        #endif
        #ifdef TOUCH_INT_GATE
        if (s_touch.available && isr_service()) {
            gpio_config_t io = {
                .pin_bit_mask = 1ULL << MOY_INPUT_TOUCH_INT,
                .mode = GPIO_MODE_INPUT,
                .intr_type = GPIO_INTR_ANYEDGE,
            };
            if (gpio_config(&io) == ESP_OK
                && gpio_isr_handler_add(MOY_INPUT_TOUCH_INT, touch_int_isr, NULL) == ESP_OK) {
                s_touch.gate = true;
            }
        }
        #endif
        s_touch_up = true;
        task_up();
    }
    *d = &s_touch;
    return true;
    #else
    (void)d;
    (void)w;
    (void)h;
    return false;
    #endif
}

bool moy_input_board_ball(moy_ball_t **b, bool *click) {
    #ifdef MOY_INPUT_BALL_UP
    if (!s_ball_up) {
        if (!isr_service()) {
            return false;
        }
        for (uintptr_t i = 0; i < 4; i++) {
            gpio_config_t io = {
                .pin_bit_mask = 1ULL << BALL_PINS[i],
                .mode = GPIO_MODE_INPUT,
                .pull_up_en = GPIO_PULLUP_ENABLE,
                .intr_type = GPIO_INTR_NEGEDGE,
            };
            gpio_config(&io);
            gpio_isr_handler_add(BALL_PINS[i], ball_isr, (void *)i);
        }
        gpio_config_t io = {
            .pin_bit_mask = 1ULL << MOY_INPUT_BALL_CLICK,
            .mode = GPIO_MODE_INPUT,
            .pull_up_en = GPIO_PULLUP_ENABLE,
        };
        gpio_config(&io);
        s_ball_up = true;
    }
    *b = &s_ball;
    *click = gpio_get_level(MOY_INPUT_BALL_CLICK) == 0;
    return true;
    #else
    (void)b;
    (void)click;
    return false;
    #endif
}

#else

bool moy_input_board_kbd(moy_kbd_t **k) {
    (void)k;
    return false;
}

bool moy_input_board_touch(moy_touchdev_t **d, int32_t w, int32_t h) {
    (void)d;
    (void)w;
    (void)h;
    return false;
}

bool moy_input_board_ball(moy_ball_t **b, bool *click) {
    (void)b;
    (void)click;
    return false;
}

void moy_input_board_kick(void) {
}

bool moy_input_board_owns_pin(int gpio) {
    (void)gpio;
    return false;
}

uint32_t moy_input_board_stack_free(void) {
    return 0;
}

#endif

// moy_light_ledc: a panel backlight driven as an LEDC PWM channel, the body
// the panel modules whose light is a plain PWM-dimmable enable pin take
// (native/p4/moy_dsi on both P4 boards, the Guition S3's moy_axs). The T-Deck's
// light is a pulse-stepped chip, not PWM: its body is moy_lcd's own.
//
// A level is moy_light.h's (0 off, 255 full). The
// channel is configured on the first call, which also takes the pin from
// whatever plain-GPIO parking the module's init gave it, and reserves its
// pair in the LEDC book first (native/moy_kernel/moy_ledc.h: the timer SOLO),
// so machine.PWM and a compiled program's PWM handles never take either.
// Timer 3 and channel 7 are the last of each, the far end from machine.PWM's
// first-free allocation.

#ifndef MOY_LIGHT_LEDC_H
#define MOY_LIGHT_LEDC_H

#include <stdbool.h>

#include "driver/gpio.h"
#include "driver/ledc.h"
#include "moy_ledc.h"
#include "moy_light.h"

#define MOY_LIGHT_LEDC_TIMER LEDC_TIMER_3
#define MOY_LIGHT_LEDC_CHAN LEDC_CHANNEL_7
#define MOY_LIGHT_LEDC_BITS 10
// Above hearing: at 5 kHz the Guition P4's backlight boost whined at any level
// below full.
#define MOY_LIGHT_LEDC_HZ 25000

// A channel that would not configure leaves the pin a plain output, lit at any
// level above off: a light that cannot dim, never one that stays dark.
static inline void moy_light_ledc_set(int gpio, bool active_low, int level) {
    static int8_t state;                // 0 unconfigured, 1 the channel, -1 plain
    uint32_t full = 1u << MOY_LIGHT_LEDC_BITS;
    uint32_t duty = level <= 0 ? 0 : level >= MOY_LIGHT_FULL ? full
                  : (uint32_t)level * full / MOY_LIGHT_FULL;
    if (state == 0) {
        ledc_timer_config_t t = {
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .duty_resolution = MOY_LIGHT_LEDC_BITS,
            .timer_num = MOY_LIGHT_LEDC_TIMER,
            .freq_hz = MOY_LIGHT_LEDC_HZ,
            .clk_cfg = LEDC_AUTO_CLK,
        };
        ledc_channel_config_t c = {
            .gpio_num = gpio,
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel = MOY_LIGHT_LEDC_CHAN,
            .timer_sel = MOY_LIGHT_LEDC_TIMER,
            .duty = duty,
            .hpoint = 0,
            .flags.output_invert = active_low ? 1 : 0,
        };
        if (moy_ledc_reserve(MOY_LIGHT_LEDC_CHAN, MOY_LIGHT_LEDC_TIMER, MOY_LEDC_LIGHT,
                             MOY_LIGHT_LEDC_HZ, MOY_LIGHT_LEDC_BITS, (int32_t)LEDC_AUTO_CLK)
            && ledc_timer_config(&t) == ESP_OK && ledc_channel_config(&c) == ESP_OK) {
            state = 1;
            return;
        }
        gpio_set_direction(gpio, GPIO_MODE_OUTPUT);
        state = -1;
    }
    if (state < 0) {
        gpio_set_level(gpio, (level > 0) != active_low);
        return;
    }
    ledc_set_duty(LEDC_LOW_SPEED_MODE, MOY_LIGHT_LEDC_CHAN, duty);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, MOY_LIGHT_LEDC_CHAN);
}

// The duty the channel holds now, of 1 << MOY_LIGHT_LEDC_BITS; -1 before the
// first level or where the channel would not configure.
static inline int moy_light_ledc_duty(void) {
    uint32_t d = ledc_get_duty(LEDC_LOW_SPEED_MODE, MOY_LIGHT_LEDC_CHAN);
    return d == LEDC_ERR_DUTY ? -1 : (int)d;
}

#endif // MOY_LIGHT_LEDC_H

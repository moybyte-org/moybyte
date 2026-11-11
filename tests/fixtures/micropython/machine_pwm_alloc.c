// ports/esp32/machine_pwm.c of MicroPython v1.28.0 (MIT licence, the
// MicroPython authors): the PWM book and its allocator, verbatim, for
// tests/test_patch_pwm_ledc_book.py.

typedef struct _chans_t {
    int8_t pin;      // Which channel has which GPIO pin assigned? (-1 if not assigned)
    int8_t timer;    // Which channel has which timer assigned? (-1 if not assigned)
    bool lightsleep; // Is light sleep enable has been set for this pin
} chans_t;

// List of PWM channels
static chans_t chans[LEDC_SPEED_MODE_MAX][LEDC_CHANNEL_MAX];

typedef struct _timers_t {
    int32_t freq;
    int8_t duty_resolution;
    ledc_clk_cfg_t clk_cfg;
} timers_t;

// List of PWM timers
static timers_t timers[LEDC_SPEED_MODE_MAX][LEDC_TIMER_MAX];

// register-unregister channel
static void register_channel(int mode, int channel, int pin, int timer) {
    chans[mode][channel].pin = pin;
    chans[mode][channel].timer = timer;
}

static void unregister_channel(int mode, int channel) {
    register_channel(mode, channel, -1, -1);
    chans[mode][channel].lightsleep = false;
}

static void unregister_timer(int mode, int timer) {
    timers[mode][timer].freq = -1; // unused timer freq is -1
    timers[mode][timer].duty_resolution = 0;
    timers[mode][timer].clk_cfg = LEDC_AUTO_CLK;
}

static void pwm_init(void) {
    for (int mode = 0; mode < LEDC_SPEED_MODE_MAX; ++mode) {
        // Initial condition: no channels assigned
        for (int channel = 0; channel < LEDC_CHANNEL_MAX; ++channel) {
            unregister_channel(mode, channel);
        }
        // Initial condition: no timers assigned
        for (int timer = 0; timer < LEDC_TIMER_MAX; ++timer) {
            unregister_timer(mode, timer);
        }
    }
}

// Returns true if the timer is in use in addition to current channel
static bool is_timer_in_use(int mode, int current_channel, int timer) {
    for (int channel = 0; channel < LEDC_CHANNEL_MAX; ++channel) {
        if ((channel != current_channel) && (chans[mode][channel].timer == timer) && (chans[mode][channel].pin >= 0)) {
            return true;
        }
    }
    return false;
}

// Deinit channel and timer if the timer is unused
static void pwm_deinit(int mode, int channel, int level) {
    // Is valid channel?
    if ((mode >= 0) && (mode < LEDC_SPEED_MODE_MAX) && (channel >= 0) && (channel < LEDC_CHANNEL_MAX)) {
        // Clean up timer if necessary
        int timer = chans[mode][channel].timer;
        if (timer >= 0) {
            if (!is_timer_in_use(mode, channel, timer)) {
                check_esp_err(ledc_timer_pause(mode, timer));
                ledc_timer_config_t ledc_timer = {
                    .deconfigure = true,
                    .speed_mode = mode,
                    .timer_num = timer,
                };
                ledc_timer_config(&ledc_timer);
                unregister_timer(mode, timer);
            }
        }

        int pin = chans[mode][channel].pin;
        if (pin >= 0) {
            // Disable LEDC output, and set idle level
            check_esp_err(ledc_stop(mode, channel, level));
            if (chans[mode][channel].lightsleep) {
                // Enable SLP_SEL to change GPIO status automantically in lightsleep.
                check_esp_err(gpio_sleep_sel_en(pin));
                chans[mode][channel].lightsleep = false;
            }
        }
        unregister_channel(mode, channel);
    }
}

// This called from Ctrl-D soft reboot
void machine_pwm_deinit_all(void) {
    if (pwm_inited) {
        for (int mode = 0; mode < LEDC_SPEED_MODE_MAX; ++mode) {
            for (int channel = 0; channel < LEDC_CHANNEL_MAX; ++channel) {
                pwm_deinit(mode, channel, 0);
            }
        }
        #if FADE
        ledc_fade_func_uninstall();
        #endif
        pwm_inited = false;
    }
}

static uint32_t find_suitable_duty_resolution(uint32_t src_clk_freq, uint32_t timer_freq) {
    unsigned int resolution = ledc_find_suitable_duty_resolution(src_clk_freq, timer_freq);
    if (resolution > UI_RES_16_BIT) {
        // limit resolution to user interface
        resolution = UI_RES_16_BIT;
    }
    // Note: On ESP32, ESP32S2, ESP32S3, ESP32C3, ESP32C2, ESP32C6, ESP32H2, ESP32P4, due to a hardware bug,
    //       100% duty cycle (i.e. 2**duty_res) is not reachable when the binded timer selects the maximum duty
    //       resolution. For example, the max duty resolution on ESP32C3 is 14-bit width, then set duty to (2**14)
    //       will mess up the duty calculation in hardware.
    // Reduce the resolution from 14 to 13 bits to resolve the hardware bug.
    if (resolution >= SOC_LEDC_TIMER_BIT_WIDTH) {
        resolution -= 1;
    }
    return resolution;
}

#if !(CONFIG_IDF_TARGET_ESP32 || CONFIG_IDF_TARGET_ESP32S2)
// This check if a clock is already set in the timer list, if yes, return the LEDC_XXX value
static ledc_clk_cfg_t find_clock_in_use() {
    for (int mode = 0; mode < LEDC_SPEED_MODE_MAX; ++mode) {
        for (int timer = 0; timer < LEDC_TIMER_MAX; ++timer) {
            if (timers[mode][timer].clk_cfg != LEDC_AUTO_CLK) {
                return timers[mode][timer].clk_cfg;
            }
        }
    }
    return LEDC_AUTO_CLK;
}

// Check if a timer is already set with a different clock source
static bool is_timer_with_different_clock(int mode, int current_timer, ledc_clk_cfg_t clk_cfg) {
    for (int timer = 0; timer < LEDC_TIMER_MAX; ++timer) {
        if ((timer != current_timer) && (clk_cfg != LEDC_AUTO_CLK) && (timers[mode][timer].clk_cfg != LEDC_AUTO_CLK) && (timers[mode][timer].clk_cfg != clk_cfg)) {
            return true;
        }
    }
    return false;
}
#endif

static void check_freq_ranges(machine_pwm_obj_t *self, int freq, int upper_freq) {
    if ((freq <= 0) || (freq > upper_freq)) {
        mp_raise_msg_varg(&mp_type_ValueError, MP_ERROR_TEXT("frequency must be from 1Hz to %dMHz"), upper_freq / 1000000);
    }
}

// Set timer frequency
static void set_freq(machine_pwm_obj_t *self, unsigned int freq) {
    self->freq = freq;
    if ((timers[self->mode][self->timer].freq != freq) || (self->lightsleep)) {
        ledc_timer_config_t timer = {};
        timer.speed_mode = self->mode;
        timer.timer_num = self->timer;
        timer.freq_hz = freq;
        timer.deconfigure = false;

        timer.clk_cfg = LEDC_AUTO_CLK;
        #if !(CONFIG_IDF_TARGET_ESP32 || CONFIG_IDF_TARGET_ESP32S2)
        ledc_clk_cfg_t clk_cfg = find_clock_in_use();
        if (clk_cfg != LEDC_AUTO_CLK) {
            timer.clk_cfg = clk_cfg;
        } else
        #endif
        if (self->lightsleep) {
            timer.clk_cfg = LEDC_USE_RC_FAST_CLK; // 8 or 20 MHz
        } else {
            #if SOC_LEDC_SUPPORT_APB_CLOCK
            timer.clk_cfg = LEDC_USE_APB_CLK; // 80 MHz
            #elif SOC_LEDC_SUPPORT_PLL_DIV_CLOCK
            timer.clk_cfg = LEDC_USE_PLL_DIV_CLK; // 60 or 80 or 96 MHz
            #elif SOC_LEDC_SUPPORT_XTAL_CLOCK
            timer.clk_cfg = LEDC_USE_XTAL_CLK; // 40 MHz
            #else
            #error No supported PWM / LEDC clocks.
            #endif

            #ifdef EMPIRIC_FREQ // ESP32 and ESP32S2 only
            if (freq < EMPIRIC_FREQ) {
                timer.clk_cfg = LEDC_USE_REF_TICK; // 1 MHz
            }
            #endif
        }
        #if !(CONFIG_IDF_TARGET_ESP32 || CONFIG_IDF_TARGET_ESP32S2)
        // Check for clock source conflict
        clk_cfg = find_clock_in_use();
        if ((clk_cfg != LEDC_AUTO_CLK) && (clk_cfg != timer.clk_cfg)) {
            mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("one or more active timers use a different clock source, not supported by the current SoC."));
        }
        #endif

        uint32_t src_clk_freq = 0;
        check_esp_err(esp_clk_tree_src_get_freq_hz(timer.clk_cfg, ESP_CLK_TREE_SRC_FREQ_PRECISION_APPROX, &src_clk_freq));
        // machine.freq(20_000_000) reduces APB_CLK_FREQ to 20MHz and the highest PWM frequency to 10MHz
        check_freq_ranges(self, freq, src_clk_freq / 2);

        // Configure the new resolution
        timer.duty_resolution = find_suitable_duty_resolution(src_clk_freq, self->freq);
        // Configure timer - Set frequency
        if ((timers[self->mode][self->timer].duty_resolution == timer.duty_resolution) && (timers[self->mode][self->timer].clk_cfg == timer.clk_cfg)) {
            check_esp_err(ledc_set_freq(self->mode, self->timer, freq));
        } else {
            check_esp_err(ledc_timer_config(&timer));
        }
        // Reset the timer if low speed
        if (self->mode == LEDC_LOW_SPEED_MODE) {
            check_esp_err(ledc_timer_rst(self->mode, self->timer));
        }
        timers[self->mode][self->timer].freq = freq;
        timers[self->mode][self->timer].duty_resolution = timer.duty_resolution;
        timers[self->mode][self->timer].clk_cfg = timer.clk_cfg;
    }
}

static bool is_free_channels(int mode, int pin) {
    for (int channel = 0; channel < LEDC_CHANNEL_MAX; ++channel) {
        if ((chans[mode][channel].pin < 0) || (chans[mode][channel].pin == pin)) {
            return true;
        }
    }
    return false;
}

static bool is_free_timers(int mode, int32_t freq) {
    for (int timer = 0; timer < LEDC_TIMER_MAX; ++timer) {
        if ((timers[mode][timer].freq < 0) || (timers[mode][timer].freq == freq)) {
            return true;
        }
    }
    return false;
}

// Find self channel or free channel
static void find_channel(machine_pwm_obj_t *self, int *ret_mode, int *ret_channel, int32_t freq) {
    // Try to find self channel first
    for (int mode = 0; mode < LEDC_SPEED_MODE_MAX; ++mode) {
        #if SOC_LEDC_SUPPORT_HS_MODE
        if (self->lightsleep && (mode == LEDC_HIGH_SPEED_MODE)) {
            continue;
        }
        #endif
        for (int channel = 0; channel < LEDC_CHANNEL_MAX; ++channel) {
            if (chans[mode][channel].pin == self->pin) {
                *ret_mode = mode;
                *ret_channel = channel;
                return;
            }
        }
    }
    // Find free channel
    for (int mode = 0; mode < LEDC_SPEED_MODE_MAX; ++mode) {
        #if SOC_LEDC_SUPPORT_HS_MODE
        if (self->lightsleep && (mode == LEDC_HIGH_SPEED_MODE)) {
            continue;
        }
        #endif
        for (int channel = 0; channel < LEDC_CHANNEL_MAX; ++channel) {
            if ((chans[mode][channel].pin < 0) && is_free_timers(mode, freq)) {
                *ret_mode = mode;
                *ret_channel = channel;
                return;
            }
        }
    }
}

// Returns timer with the same frequency, freq == -1 means free timer
static void find_timer(machine_pwm_obj_t *self, int freq, int *ret_mode, int *ret_timer) {
    for (int mode = 0; mode < LEDC_SPEED_MODE_MAX; ++mode) {
        #if SOC_LEDC_SUPPORT_HS_MODE
        if (self->lightsleep && (mode == LEDC_HIGH_SPEED_MODE)) {
            continue;
        }
        #endif
        if (is_free_channels(mode, self->pin)) {
            for (int timer = 0; timer < LEDC_TIMER_MAX; ++timer) {
                if (timers[mode][timer].freq == freq) {
                    *ret_mode = mode;
                    *ret_timer = timer;
                    return;
                }
            }
        }
    }
}

// Try to find a timer with the same frequency in the current mode, otherwise in the next mode.
// If no existing timer and channel was found, then try to find free timer in any mode.
// If the mode or channel is changed, release the channel and register a new channel in the next mode.
static void select_timer(machine_pwm_obj_t *self, int freq) {
    // mode, channel, timer may be -1(not defined) or actual values
    int mode = -1;
    int timer = -1;
    // Check if an already running timer with the required frequency is running
    find_timer(self, freq, &mode, &timer);
    if (timer < 0) {
        // Try to reuse self timer
        if ((self->mode >= 0) && (self->channel >= 0)) {
            if (!is_timer_in_use(self->mode, self->channel, self->timer)) {
                mode = self->mode;
                timer = self->timer;
            }
        }
        // If no existing timer and channel was found, then try to find free timer in any mode
        if (timer < 0) {
            find_timer(self, -1, &mode, &timer);
        }
    }
    if (timer < 0) {
        mp_raise_msg_varg(&mp_type_RuntimeError, MP_ERROR_TEXT("out of %sPWM timers:%d"), self->lightsleep ? "light sleep capable " : "", self->lightsleep ? LEDC_TIMER_MAX : LEDC_SPEED_MODE_MAX *LEDC_TIMER_MAX);
    }
    // If the timer is found, then register
    if (self->timer != timer) {
        unregister_channel(self->mode, self->channel);
        // Rregister the channel to the timer
        self->mode = mode;
        self->timer = timer;
        register_channel(self->mode, self->channel, -1, self->timer);
    }
    #if !(CONFIG_IDF_TARGET_ESP32 || CONFIG_IDF_TARGET_ESP32S2)
    if (is_timer_with_different_clock(self->mode, self->timer, timers[self->mode][self->timer].clk_cfg)) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("one or more active timers use a different clock source, not supported by the current SoC."));
    }
    #endif
}

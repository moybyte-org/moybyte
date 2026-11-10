"""tools/patch_pwm_ledc_book.py: machine.PWM takes its LEDC channels and
timers from the kernel's book (native/moy_kernel/moy_ledc.h), every hunk or
none.

The allocator is the esp32 port's own text (tests/fixtures/micropython/
machine_pwm_alloc.c, MicroPython 1.28's book, deinit, set_freq and timer
selection, verbatim), patched and compiled on the host with the real book,
and driven as machine.PWM's constructor, freq() and deinit() drive it, beside
a compiled program's PWM handle walking the book as moy_devbus.c does and the
light's reserved pair. The LEDC driver under it is a stub that checks every
write against the book -- a timer is configured only while SETTING and
deconfigured only while CLOSING -- and keeps what each channel and timer
drive, so a run ends by asking whether every live PWM outputs the frequency
its holder set."""

import os
import re
import subprocess

import pytest

from runtime import native_build
from tools import patch_pwm_ledc_book as p

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KERNEL = os.path.join(ROOT, "native", "moy_kernel")
with open(os.path.join(ROOT, "tests", "fixtures", "micropython", "machine_pwm_alloc.c"),
          encoding="utf-8") as _fh:
    STOCK = _fh.read()

# What the allocator needs from the port and ESP-IDF, for an S3 or a P4 (one
# speed mode, eight channels, four timers), the stub driver among it.
PRELUDE = r"""
#include <setjmp.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "moy_ledc.h"

void moy_lease_lock(void) {}
void moy_lease_unlock(void) {}

#define LEDC_LOW_SPEED_MODE 0
#define LEDC_SPEED_MODE_MAX 1
#define LEDC_CHANNEL_MAX 8
#define LEDC_TIMER_MAX 4
#define SOC_LEDC_SUPPORT_HS_MODE 0
#define SOC_LEDC_SUPPORT_APB_CLOCK 1
#define SOC_LEDC_TIMER_BIT_WIDTH 14
#define FADE 0
#define UI_RES_16_BIT 16
typedef enum { LEDC_AUTO_CLK = 0, LEDC_USE_APB_CLK = 4, LEDC_USE_RC_FAST_CLK = 8 } ledc_clk_cfg_t;
typedef struct {
    int speed_mode, duty_resolution, timer_num;
    uint32_t freq_hz;
    ledc_clk_cfg_t clk_cfg;
    bool deconfigure;
} ledc_timer_config_t;
#define ESP_CLK_TREE_SRC_FREQ_PRECISION_APPROX 0
#define MP_ERROR_TEXT(s) (s)
static int mp_type_RuntimeError, mp_type_ValueError;
static jmp_buf g_jb;
static const char *g_err;
#define mp_raise_msg(t, m) do { (void)(t); g_err = (m); longjmp(g_jb, 1); } while (0)
#define mp_raise_msg_varg(t, m, ...) do { (void)(t); g_err = (m); longjmp(g_jb, 1); } while (0)
#define check_esp_err(x) ((void)(x))

typedef struct _machine_pwm_obj_t {
    int pin; int8_t mode; int8_t channel; int8_t timer; bool lightsleep; int32_t freq;
} machine_pwm_obj_t;
static bool pwm_inited = false;

// The hardware: what each timer counts at, which timer each channel rides.
static int hw_freq[LEDC_TIMER_MAX];
static int hw_chan[LEDC_CHANNEL_MAX] = { -1, -1, -1, -1, -1, -1, -1, -1 };
static int g_bad;

static void bad(const char *what, int t) {
    printf("BAD %s timer %d state %d\n", what, t, moy_ledc_timer_state(t));
    g_bad++;
}

static int ledc_timer_config(const ledc_timer_config_t *c) {
    int st = moy_ledc_timer_state(c->timer_num);
    if (c->deconfigure) {
        if (st != MOY_LEDC_T_CLOSING) bad("deconfigure", c->timer_num);
        hw_freq[c->timer_num] = 0;
    } else {
        if (st != MOY_LEDC_T_SETTING) bad("configure", c->timer_num);
        hw_freq[c->timer_num] = (int)c->freq_hz;
    }
    return 0;
}
static int ledc_set_freq(int mode, int t, uint32_t f) {
    (void)mode;
    if (moy_ledc_timer_state(t) != MOY_LEDC_T_SETTING) bad("set_freq", t);
    hw_freq[t] = (int)f;
    return 0;
}
static int ledc_timer_pause(int mode, int t) { (void)mode; (void)t; return 0; }
static int ledc_timer_rst(int mode, int t) { (void)mode; (void)t; return 0; }
static int ledc_stop(int mode, int ch, int level) { (void)mode; (void)level; hw_chan[ch] = -1; return 0; }
static int gpio_sleep_sel_en(int pin) { (void)pin; return 0; }
static int esp_clk_tree_src_get_freq_hz(ledc_clk_cfg_t c, int p, uint32_t *hz) {
    (void)c; (void)p; *hz = 80000000u; return 0;
}
static uint32_t ledc_find_suitable_duty_resolution(uint32_t src, uint32_t freq) {
    uint32_t r = 0;
    while (r < 20 && ((uint64_t)freq << (r + 1)) <= src) r++;
    return r;
}
"""

DRIVER = r"""
static machine_pwm_obj_t py[8];
static int py_freq[8];

static void py_open(int i, int pin, int freq) {
    machine_pwm_obj_t *self = &py[i];
    *self = (machine_pwm_obj_t){ pin, -1, -1, -1, false, -1 };
    if (!pwm_inited) { pwm_init(); pwm_inited = true; }
    if (setjmp(g_jb)) { printf("py%d raise %s\n", i, g_err); self->channel = -1; return; }
    int mode = -1, channel = -1;
    find_channel(self, &mode, &channel, freq);
    if (channel < 0) mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("out of PWM channels"));
    self->mode = mode;
    self->channel = channel;
    select_timer(self, freq);
    set_freq(self, freq);
    register_channel(self->mode, self->channel, self->pin, self->timer);   // apply_duty's
    hw_chan[channel] = self->timer;
    py_freq[i] = freq;
    printf("py%d ch %d timer %d\n", i, channel, self->timer);
}

static void py_set(int i, int freq) {
    machine_pwm_obj_t *self = &py[i];
    if (setjmp(g_jb)) { printf("py%d raise %s\n", i, g_err); return; }
    if (freq == timers[self->mode][self->timer].freq) { py_freq[i] = freq; return; }
    select_timer(self, freq);
    set_freq(self, freq);
    register_channel(self->mode, self->channel, self->pin, self->timer);
    hw_chan[self->channel] = self->timer;
    py_freq[i] = freq;
    printf("py%d timer %d\n", i, self->timer);
}

static void py_close(int i) {
    pwm_deinit(py[i].mode, py[i].channel, 0);
    py[i].channel = -1;
}

// A compiled program's handle, as moy_devbus.c walks the book.
static int dev_ch[8], dev_freq[8];
static uint32_t dev_pid[8];

static int dev_timer(int ch, uint32_t freq) {
    for (int t = 0; t < LEDC_TIMER_MAX; t++) {
        if (moy_ledc_timer_join(ch, t, freq)) return t;
    }
    int t = moy_ledc_chan_timer(ch);
    if (t < 0 || !moy_ledc_timer_retune(t, ch)) {
        for (t = 0; t < LEDC_TIMER_MAX && !moy_ledc_timer_join(ch, t, MOY_LEDC_FRESH); t++) {
        }
        if (t == LEDC_TIMER_MAX) return -1;
    }
    ledc_timer_config_t c = { 0, 13, t, freq, LEDC_USE_APB_CLK, false };
    ledc_timer_config(&c);
    moy_ledc_timer_set(t, freq, 13, LEDC_USE_APB_CLK);
    return t;
}

static void dev_open(int i, uint32_t pid, int freq) {
    int ch = moy_ledc_chan_take(pid, true);
    int t = ch < 0 ? -1 : dev_timer(ch, (uint32_t)freq);
    if (t < 0) {
        if (ch >= 0) moy_ledc_chan_drop(ch, pid);
        dev_ch[i] = -1;
        printf("dev%d full\n", i);
        return;
    }
    dev_ch[i] = ch; dev_pid[i] = pid; dev_freq[i] = freq;
    hw_chan[ch] = t;
    printf("dev%d ch %d timer %d\n", i, ch, t);
}

static void dev_set(int i, int freq) {
    int t = dev_timer(dev_ch[i], (uint32_t)freq);
    if (t < 0) { printf("dev%d full\n", i); return; }
    hw_chan[dev_ch[i]] = t;
    dev_freq[i] = freq;
    printf("dev%d timer %d\n", i, t);
}

static void dev_close(int i) {
    hw_chan[dev_ch[i]] = -1;
    int t = moy_ledc_chan_drop(dev_ch[i], dev_pid[i]);
    if (t >= 0) {
        ledc_timer_config_t c = { 0, 0, t, 0, LEDC_AUTO_CLK, true };
        ledc_timer_config(&c);
        moy_ledc_timer_closed(t);
    }
    dev_ch[i] = -1;
}

int main(int argc, char **argv) {
    for (int i = 0; i < 8; i++) { py[i].channel = -1; dev_ch[i] = -1; }
    int light = 0;
    for (int a = 1; a < argc; a++) {
        int i = 0, x = 0, y = 0;
        const char *w = argv[a];
        if (sscanf(w, "py%d=%d@%d", &i, &x, &y) == 3) py_open(i, x, y);
        else if (sscanf(w, "py%d@%d", &i, &y) == 2) py_set(i, y);
        else if (sscanf(w, "py%d.close", &i) == 1) py_close(i);
        else if (sscanf(w, "dev%d=%d@%d", &i, &x, &y) == 3) dev_open(i, (uint32_t)x, y);
        else if (sscanf(w, "dev%d@%d", &i, &y) == 2) dev_set(i, y);
        else if (sscanf(w, "dev%d.close", &i) == 1) dev_close(i);
        else if (strcmp(w, "light") == 0) {
            light = moy_ledc_reserve(7, 3, MOY_LEDC_LIGHT, 25000, 10, LEDC_AUTO_CLK);
            hw_chan[7] = 3; hw_freq[3] = 25000;
        } else if (strcmp(w, "vm_end") == 0) {
            machine_pwm_deinit_all();
            moy_ledc_vm_drop();
            for (int k = 0; k < 8; k++) py[k].channel = -1;
        } else { printf("? %s\n", w); return 2; }
    }
    // Every live PWM drives what its holder set, on a channel no other holds.
    int owner[LEDC_CHANNEL_MAX] = { 0 };
    for (int i = 0; i < 8; i++) {
        int ch = py[i].channel;
        if (ch >= 0 && (owner[ch]++ || hw_chan[ch] < 0 || hw_freq[hw_chan[ch]] != py_freq[i]))
            printf("WRONG py%d\n", i);
        ch = dev_ch[i];
        if (ch >= 0 && (owner[ch]++ || hw_chan[ch] < 0 || hw_freq[hw_chan[ch]] != dev_freq[i]))
            printf("WRONG dev%d\n", i);
    }
    if (light && (owner[7]++ || hw_freq[hw_chan[7]] != 25000)) printf("WRONG light\n");
    printf("bad %d\n", g_bad);
    return 0;
}
"""


def _tree(tmp_path, text):
    f = tmp_path / p.FILE
    f.parent.mkdir(parents=True)
    f.write_text(text, encoding="utf-8")
    return f


_EXE = {}


def _exe(tmp_path_factory, patched):
    if patched not in _EXE:
        cc = native_build.cc()
        if cc is None:
            pytest.skip("no C compiler")
        d = tmp_path_factory.mktemp("pwm")
        src = d / "alloc.c"
        src.write_text(PRELUDE + (p.patch_text(STOCK) if patched else STOCK) + DRIVER,
                       encoding="utf-8")
        exe = d / "alloc"
        subprocess.run([cc, "-std=gnu11", "-Wall", "-Werror", "-Wno-unused-function",
                        "-I", KERNEL, "-o", str(exe), str(src),
                        os.path.join(KERNEL, "moy_ledc.c")], check=True)
        _EXE[patched] = str(exe)
    return _EXE[patched]


def _run(tmp_path_factory, *script, patched=True):
    out = subprocess.run([_exe(tmp_path_factory, patched)] + list(script), check=True,
                         capture_output=True, text=True).stdout
    lines = out.splitlines()
    assert lines[-1].startswith("bad ")
    return lines


def _clean(lines):
    assert lines[-1] == "bad 0" and not [ln for ln in lines if ln.startswith(("BAD", "WRONG"))], lines


def test_a_servo_and_a_buzzer_beside_compiled_handles_and_the_light(tmp_path_factory):
    """Two Python frequencies, two compiled ones, the light: every frequency
    has a timer, the handle at the servo's frequency shares the servo's, and
    nobody configures a timer they do not hold SETTING."""
    got = _run(tmp_path_factory, "light", "py0=1@50", "py1=2@2000", "dev0=9@1000",
               "dev1=9@50")
    _clean(got)
    assert got[:4] == ["py0 ch 0 timer 0", "py1 ch 1 timer 1", "dev0 ch 6 timer 2",
                       "dev1 ch 5 timer 0"]


def test_a_shared_timer_is_never_retuned_or_closed_under_another_holder(tmp_path_factory):
    got = _run(tmp_path_factory, "py0=1@50", "dev0=9@50", "py0@1000", "py1=2@50",
               "py1.close", "dev0@2000")
    _clean(got)
    # py0 moved off the shared timer to its own; dev0, alone on timer 0 at
    # the end, retunes it in place.
    assert "py0 timer 1" in got and got[-2] == "dev0 timer 0"


def test_the_vms_end_gives_back_its_channels_and_the_next_vm_starts_empty(tmp_path_factory):
    got = _run(tmp_path_factory, "py0=1@50", "py1=2@1000", "dev0=9@1000", "vm_end",
               "py0=3@1000", "py1=4@440")
    _clean(got)
    assert got[-3:-1] == ["py0 ch 0 timer 1", "py1 ch 1 timer 0"]


def test_out_of_timers_is_machine_pwms_own_raise(tmp_path_factory):
    got = _run(tmp_path_factory, "light", "dev0=9@1000", "py0=1@50", "py1=2@440",
               "py2=3@2000", "dev1=9@3000")
    _clean(got)
    assert got[-3:-1] == ["py2 raise out of PWM channels", "dev1 full"]


def test_stock_machine_pwm_drives_a_compiled_handles_timer(tmp_path_factory):
    """The collision the patch removes: unpatched, machine.PWM's own book does
    not know the handle's timer and configures it at its own frequency."""
    got = _run(tmp_path_factory, "dev0=9@1000", "py0=1@50", patched=False)
    assert "WRONG dev0" in got and got[-1] != "bad 0"


def test_a_patched_tree_is_left_as_it_is(tmp_path):
    f = _tree(tmp_path, STOCK)
    assert p.apply(str(tmp_path)) == "patched"
    once = f.read_text(encoding="utf-8")
    assert p.MARKER in once
    assert p.apply(str(tmp_path)) == "already"
    assert f.read_text(encoding="utf-8") == once


def test_an_allocator_that_changed_shape_is_refused_and_not_written(tmp_path):
    f = _tree(tmp_path, re.sub(r"if \(timers\[mode\]\[timer\]\.freq == freq\) \{",
                               "if (timers[mode][timer].freq == freq){", STOCK))
    before = f.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        p.apply(str(tmp_path))
    assert f.read_text(encoding="utf-8") == before

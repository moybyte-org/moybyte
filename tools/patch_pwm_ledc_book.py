#!/usr/bin/env python3
"""machine.PWM takes its LEDC channels and timers from the kernel's book.

    python3 tools/patch_pwm_ledc_book.py <micropython checkout>

The esp32 port's `machine.PWM` keeps a book of the LEDC peripheral in static
arrays and hands out the first free channel and timer it sees there. The
console drives LEDC outside it: a compiled program's PWM handles
(native/moy_kernel/moy_devbus.c) and a panel's backlight
(native/moy_glass/moy_light_ledc.h). Stock, a Python program could take, or
retune, a channel or timer one of them was driving.

The patch makes machine.PWM ask the kernel's LEDC book
(native/moy_kernel/moy_ledc.h), with MOY_LEDC_VM as the holder, at each place
it decides:

  * a free channel is one the book has free (or already the VM's), claimed
    when machine.PWM picks it;
  * a timer at a frequency is one the book has READY at it, whoever set it,
    joined, MicroPython's record of it taken from the book's; a free timer is
    one the book has FREE, now SETTING; its own timer it retunes only where the
    book says no other channel rides it;
  * set_freq configures a timer only while the book has it SETTING, and
    reports what it wrote (READY);
  * pwm_deinit stops the channel's output, then drops the channel from the
    book, and deconfigures its timer only when the book hands it back
    CLOSING (its last channel, every holder's counted).

machine.PWM's own arrays stay its record of its own channels. At the VM's end
its deinit drops every channel it holds, and moy_lease_vm_stop drops from the
book whatever a raise left half taken, so the two agree when the next VM
starts its arrays from empty.

The book has the low-speed mode only, which is every mode the S3 and the P4
have (a static assert says so).

Applied by `moybyte_patch_pwm_ledc_book` in tools/esp32_build_lib.sh, to
`ports/esp32/machine_pwm.c`. All-or-nothing: every hunk must match exactly
once or nothing is written and the exit is non-zero. A file already carrying
the marker is left as it is.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: the LEDC book"
FILE = "ports/esp32/machine_pwm.c"

HUNKS = (
    # the book, and MicroPython's record of a timer taken from it
    ("static void pwm_init(void) {\n",
     "// %s (native/moy_kernel/moy_ledc.h): every\n"
     "// holder's channels and timers, machine.PWM's as MOY_LEDC_VM\n"
     "// (tools/patch_pwm_ledc_book.py).\n"
     "#include \"moy_ledc.h\"\n"
     "_Static_assert(LEDC_SPEED_MODE_MAX == 1, \"the LEDC book is the low-speed mode's\");\n"
     "\n"
     "// The record of timer `timer` made the book's: a READY timer's configuration\n"
     "// as its holder wrote it, any other unset, so set_freq configures it whole.\n"
     "static void moy_pwm_mirror(int mode, int timer) {\n"
     "    uint32_t freq = 0;\n"
     "    uint8_t bits = 0;\n"
     "    int32_t clk = 0;\n"
     "    if (moy_ledc_timer_cfg(timer, &freq, &bits, &clk)) {\n"
     "        timers[mode][timer].freq = (int32_t)freq;\n"
     "        timers[mode][timer].duty_resolution = (int8_t)bits;\n"
     "        timers[mode][timer].clk_cfg = (ledc_clk_cfg_t)clk;\n"
     "    } else {\n"
     "        unregister_timer(mode, timer);\n"
     "    }\n"
     "}\n"
     "\n"
     "static void pwm_init(void) {\n" % MARKER),
    # is_timer_in_use: every holder's channels
    ("    for (int channel = 0; channel < LEDC_CHANNEL_MAX; ++channel) {\n"
     "        if ((channel != current_channel) && (chans[mode][channel].timer == timer) "
     "&& (chans[mode][channel].pin >= 0)) {\n"
     "            return true;\n"
     "        }\n"
     "    }\n"
     "    return false;\n",
     "    (void)mode;\n"
     "    return moy_ledc_timer_users(timer, current_channel) > 0;\n"),
    # pwm_deinit: the output stopped, then the book's channel, then its timer
    # only when the book hands it back
    ("        // Clean up timer if necessary\n"
     "        int timer = chans[mode][channel].timer;\n"
     "        if (timer >= 0) {\n"
     "            if (!is_timer_in_use(mode, channel, timer)) {\n"
     "                check_esp_err(ledc_timer_pause(mode, timer));\n"
     "                ledc_timer_config_t ledc_timer = {\n"
     "                    .deconfigure = true,\n"
     "                    .speed_mode = mode,\n"
     "                    .timer_num = timer,\n"
     "                };\n"
     "                ledc_timer_config(&ledc_timer);\n"
     "                unregister_timer(mode, timer);\n"
     "            }\n"
     "        }\n"
     "\n"
     "        int pin = chans[mode][channel].pin;\n"
     "        if (pin >= 0) {\n"
     "            // Disable LEDC output, and set idle level\n"
     "            check_esp_err(ledc_stop(mode, channel, level));\n"
     "            if (chans[mode][channel].lightsleep) {\n"
     "                // Enable SLP_SEL to change GPIO status automantically in lightsleep.\n"
     "                check_esp_err(gpio_sleep_sel_en(pin));\n"
     "                chans[mode][channel].lightsleep = false;\n"
     "            }\n"
     "        }\n"
     "        unregister_channel(mode, channel);\n",
     "        int pin = chans[mode][channel].pin;\n"
     "        if (pin >= 0) {\n"
     "            // Disable LEDC output, and set idle level\n"
     "            check_esp_err(ledc_stop(mode, channel, level));\n"
     "            if (chans[mode][channel].lightsleep) {\n"
     "                // Enable SLP_SEL to change GPIO status automantically in lightsleep.\n"
     "                check_esp_err(gpio_sleep_sel_en(pin));\n"
     "                chans[mode][channel].lightsleep = false;\n"
     "            }\n"
     "        }\n"
     "        // Moybyte: the channel back to the book, after its output stopped;\n"
     "        // its timer deconfigured only where the book hands it back CLOSING.\n"
     "        int timer = moy_ledc_chan_drop(channel, MOY_LEDC_VM);\n"
     "        if (timer >= 0) {\n"
     "            ledc_timer_pause(mode, timer);\n"
     "            ledc_timer_config_t ledc_timer = {\n"
     "                .deconfigure = true,\n"
     "                .speed_mode = mode,\n"
     "                .timer_num = timer,\n"
     "            };\n"
     "            ledc_timer_config(&ledc_timer);\n"
     "            unregister_timer(mode, timer);\n"
     "            moy_ledc_timer_closed(timer);\n"
     "        }\n"
     "        unregister_channel(mode, channel);\n"),
    # set_freq: only a timer the book has SETTING is configured
    ("    if ((timers[self->mode][self->timer].freq != freq) || (self->lightsleep)) {\n",
     "    if ((timers[self->mode][self->timer].freq != freq)\n"
     "        || (moy_ledc_timer_state(self->timer) == MOY_LEDC_T_SETTING)) {\n"),
    ("        timers[self->mode][self->timer].clk_cfg = timer.clk_cfg;\n",
     "        timers[self->mode][self->timer].clk_cfg = timer.clk_cfg;\n"
     "        moy_ledc_timer_set(self->timer, freq, timer.duty_resolution, timer.clk_cfg);\n"),
    # is_free_channels
    ("        if ((chans[mode][channel].pin < 0) || (chans[mode][channel].pin == pin)) {\n",
     "        if (((chans[mode][channel].pin < 0) && moy_ledc_chan_open(channel, MOY_LEDC_VM))\n"
     "            || (chans[mode][channel].pin == pin)) {\n"),
    # is_free_timers
    ("        if ((timers[mode][timer].freq < 0) || (timers[mode][timer].freq == freq)) {\n",
     "        if (moy_ledc_timer_usable(timer, freq < 0 ? MOY_LEDC_FRESH : (uint32_t)freq)) {\n"),
    # find_channel: a free channel, claimed
    ("            if ((chans[mode][channel].pin < 0) && is_free_timers(mode, freq)) {\n",
     "            if ((chans[mode][channel].pin < 0) && is_free_timers(mode, freq)\n"
     "                && moy_ledc_chan_claim(channel, MOY_LEDC_VM)) {\n"),
    # find_timer: a timer at this frequency (or a free one, freq -1), joined
    ("                if (timers[mode][timer].freq == freq) {\n",
     "                if (moy_ledc_timer_join(self->channel, timer,\n"
     "                    freq < 0 ? MOY_LEDC_FRESH : (uint32_t)freq)) {\n"
     "                    moy_pwm_mirror(mode, timer);\n"),
    # select_timer: its own timer retuned only where it rides it alone
    ("            if (!is_timer_in_use(self->mode, self->channel, self->timer)) {\n",
     "            if (!is_timer_in_use(self->mode, self->channel, self->timer)\n"
     "                && moy_ledc_timer_retune(self->timer, self->channel)) {\n"),
)


def patch_text(text):
    """The patched source, or SystemExit naming the hunk that does not match
    exactly once."""
    for i, (stock, patched) in enumerate(HUNKS):
        n = text.count(stock)
        if n != 1:
            sys.exit("!! machine.PWM LEDC book patch did not apply -- hunk %d "
                     "matches %d times in %s (the stock allocator changed shape); "
                     "nothing written" % (i, n, FILE))
        text = text.replace(stock, patched)
    return text


def apply(mpy_dir):
    """Patch the checkout under `mpy_dir`; returns "patched", "already", or
    raises SystemExit when a hunk does not match exactly once."""
    path = os.path.join(mpy_dir, FILE)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if MARKER in text:
        return "already"
    text = patch_text(text)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return "patched"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mpy_dir", help="the MicroPython checkout to patch "
                                    "(its ports/esp32 is what changes)")
    a = ap.parse_args(argv)
    if apply(a.mpy_dir) == "patched":
        print("== patched %s: machine.PWM takes from the LEDC book" % FILE)


if __name__ == "__main__":
    main()

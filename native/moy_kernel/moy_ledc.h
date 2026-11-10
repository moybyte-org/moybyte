// moy_ledc: the LEDC peripheral's book, the kernel's. Which low-speed channel
// and timer each holder has, so MicroPython's machine.PWM (patched to ask it,
// tools/patch_pwm_ledc_book.py), a compiled program's PWM handles
// (moy_devbus.c) and a panel's backlight (native/moy_glass/moy_light_ledc.h)
// never drive one another's.
//
// A CHANNEL is one holder's: a process's pid, MOY_LEDC_VM (machine.PWM's,
// for every Python process alike) or MOY_LEDC_LIGHT. machine.PWM takes from
// the bottom and the handles from the top, so the two meet last.
//
// A TIMER is shared by frequency: channels at one frequency ride one timer,
// whoever holds them, and its configuration (frequency, duty bits, clock) is
// the book's record of what the holder that set it wrote. A timer is FREE;
// SETTING while one holder configures it (taken fresh, or retuned by its only
// channel), joinable by none; READY once that holder reports what it wrote,
// joinable at that frequency; CLOSING while its last channel's holder
// deconfigures it. The light's timer is SOLO: never joined.
//
// The hardware is the callers': the book records, and a holder configures
// only a timer the book handed it SETTING and deconfigures only one it handed
// back CLOSING. A timer whose last channel moved to another is FREE again and
// left as it was; its next holder configures it whole.
//
// THE VM'S END: moy_lease_vm_stop drops every MOY_LEDC_VM channel (machine.PWM's
// own deinit ran first, at the VM's end; this takes what a raise left half
// taken), so the book and machine.PWM's static state agree when the next VM
// starts its own from empty.
//
// Every call holds the lease table's lock (moy_lease_lock), so any task may
// make one; never from an ISR.

#ifndef MOY_LEDC_H
#define MOY_LEDC_H

#include <stdbool.h>
#include <stdint.h>

#define MOY_LEDC_CHANNELS 8
#define MOY_LEDC_TIMERS 4
#define MOY_LEDC_VM 0xFFFFFFFFu         // machine.PWM's
#define MOY_LEDC_LIGHT 0xFFFFFFFEu      // the panel's backlight
#define MOY_LEDC_FRESH 0xFFFFFFFFu      // join: a FREE timer, to configure

enum { MOY_LEDC_T_FREE = 0, MOY_LEDC_T_SETTING, MOY_LEDC_T_READY, MOY_LEDC_T_CLOSING };

// A free channel for `holder` (nonzero), the lowest or, `high`, the highest:
// -1 for none. Claim: channel `ch` for `holder` where it is free or already
// the holder's. Open: whether it is free or the holder's.
int moy_ledc_chan_take(uint32_t holder, bool high);
bool moy_ledc_chan_claim(int ch, uint32_t holder);
bool moy_ledc_chan_open(int ch, uint32_t holder);
// Drop the holder's channel: the timer it was the last channel of, now
// CLOSING (the caller deconfigures it, then calls timer_closed), or -1.
int moy_ledc_chan_drop(int ch, uint32_t holder);
uint32_t moy_ledc_chan_holder(int ch);          // 0: free
int moy_ledc_chan_timer(int ch);                // -1: none

// Channel `ch` (taken) onto timer `t`: at `freq`, a READY timer at that
// frequency, shared; MOY_LEDC_FRESH, a FREE timer, now SETTING. The channel
// leaves the timer it rode. False: `t` is not that.
bool moy_ledc_timer_join(int ch, int t, uint32_t freq);
// Timer `t`, ridden by `ch` alone, SETTING for a new frequency: false where
// another channel rides it or it is the light's.
bool moy_ledc_timer_retune(int t, int ch);
// What the holder of a SETTING timer wrote: READY.
void moy_ledc_timer_set(int t, uint32_t freq, uint8_t bits, int32_t clk);
// A CLOSING timer deconfigured: FREE.
void moy_ledc_timer_closed(int t);
// Whether a channel could join `t` at `freq`: FREE, or READY at it and not
// SOLO (MOY_LEDC_FRESH: FREE only).
bool moy_ledc_timer_usable(int t, uint32_t freq);
// The channels on `t` besides `except` (-1: every one).
int moy_ledc_timer_users(int t, int except);
// A READY timer's record: false for any other.
bool moy_ledc_timer_cfg(int t, uint32_t *freq, uint8_t *bits, int32_t *clk);
int moy_ledc_timer_state(int t);

// The light's pair, at boot: channel `ch` and timer `t` both free, now the
// light's, the timer SOLO and READY at what it writes. False: either taken.
bool moy_ledc_reserve(int ch, int t, uint32_t holder, uint32_t freq, uint8_t bits,
                      int32_t clk);

// THE VM'S END: every MOY_LEDC_VM channel dropped and every timer it closed
// FREE; how many channels.
uint32_t moy_ledc_vm_drop(void);
// Everything FREE (a host test's start).
void moy_ledc_reset(void);

#endif // MOY_LEDC_H

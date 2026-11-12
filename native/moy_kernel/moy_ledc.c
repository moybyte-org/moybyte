// moy_ledc: the LEDC peripheral's book (moy_ledc.h has the contract).

#include "moy_platform.h"

#include "moy_ledc.h"

#include <string.h>

#include "moy_lease.h"
#include "moy_sync.h"

typedef struct {
    uint32_t holder;            // 0: free
    uint8_t timer1;             // its timer + 1, 0: none
} chan_t;

typedef struct {
    uint8_t state;              // MOY_LEDC_T_*
    uint8_t users;              // the channels riding it
    bool solo;
    uint8_t bits;
    uint32_t freq;
    int32_t clk;
    uint32_t closer;            // a CLOSING timer's holder
} tim_t;

typedef struct {
    chan_t chan[MOY_LEDC_CHANNELS];
    tim_t tim[MOY_LEDC_TIMERS];
} book_t;

static book_t *s_book;

// The book, made at its first use (moy_sync.h's THE TABLE): every entry
// calls it before its lock, and answers as for no channel where it is NULL.
static book_t *moy_ledc_book(void) {
    return MOY_SYNC_TABLE(s_book);
}

static bool chan_ok(int ch) {
    return ch >= 0 && ch < MOY_LEDC_CHANNELS;
}

static bool tim_ok(int t) {
    return t >= 0 && t < MOY_LEDC_TIMERS;
}

static void tim_free(int t) {
    memset(&s_book->tim[t], 0, sizeof(s_book->tim[t]));
}

// `ch` off its timer: the timer when it was its last channel, else -1.
static int detach(int ch) {
    int t = (int)s_book->chan[ch].timer1 - 1;
    s_book->chan[ch].timer1 = 0;
    if (t < 0) {
        return -1;
    }
    if (s_book->tim[t].users > 0) {
        s_book->tim[t].users--;
    }
    return s_book->tim[t].users == 0 ? t : -1;
}

// `ch` off its timer for another: a timer it leaves empty is FREE, as it was.
static void move_off(int ch) {
    int t = detach(ch);
    if (t >= 0 && s_book->tim[t].state != MOY_LEDC_T_CLOSING) {
        tim_free(t);
    }
}

static void attach(int ch, int t) {
    s_book->chan[ch].timer1 = (uint8_t)(t + 1);
    s_book->tim[t].users++;
}

int moy_ledc_chan_take(uint32_t holder, bool high) {
    if (holder == 0u) {
        return -1;
    }
    if (moy_ledc_book() == NULL) {
        return -1;
    }
    moy_lease_lock();
    int got = -1;
    for (int i = 0; i < MOY_LEDC_CHANNELS && got < 0; i++) {
        int ch = high ? MOY_LEDC_CHANNELS - 1 - i : i;
        if (s_book->chan[ch].holder == 0u) {
            s_book->chan[ch].holder = holder;
            got = ch;
        }
    }
    moy_lease_unlock();
    return got;
}

bool moy_ledc_chan_claim(int ch, uint32_t holder) {
    if (!chan_ok(ch) || holder == 0u) {
        return false;
    }
    if (moy_ledc_book() == NULL) {
        return false;
    }
    moy_lease_lock();
    bool ok = s_book->chan[ch].holder == 0u || s_book->chan[ch].holder == holder;
    if (ok) {
        s_book->chan[ch].holder = holder;
    }
    moy_lease_unlock();
    return ok;
}

bool moy_ledc_chan_open(int ch, uint32_t holder) {
    if (!chan_ok(ch)) {
        return false;
    }
    if (moy_ledc_book() == NULL) {
        return false;
    }
    moy_lease_lock();
    bool ok = s_book->chan[ch].holder == 0u || s_book->chan[ch].holder == holder;
    moy_lease_unlock();
    return ok;
}

int moy_ledc_chan_drop(int ch, uint32_t holder) {
    if (!chan_ok(ch) || holder == 0u) {
        return -1;
    }
    if (moy_ledc_book() == NULL) {
        return -1;
    }
    moy_lease_lock();
    int closing = -1;
    if (s_book->chan[ch].holder == holder) {
        int t = detach(ch);
        s_book->chan[ch].holder = 0u;
        if (t >= 0) {
            if (s_book->tim[t].state == MOY_LEDC_T_FREE || s_book->tim[t].state == MOY_LEDC_T_CLOSING) {
                tim_free(t);
            } else {
                s_book->tim[t].state = MOY_LEDC_T_CLOSING;
                s_book->tim[t].closer = holder;
                closing = t;
            }
        }
    }
    moy_lease_unlock();
    return closing;
}

uint32_t moy_ledc_chan_holder(int ch) {
    if (!chan_ok(ch)) {
        return 0u;
    }
    if (moy_ledc_book() == NULL) {
        return 0u;
    }
    moy_lease_lock();
    uint32_t h = s_book->chan[ch].holder;
    moy_lease_unlock();
    return h;
}

int moy_ledc_chan_timer(int ch) {
    if (!chan_ok(ch)) {
        return -1;
    }
    if (moy_ledc_book() == NULL) {
        return -1;
    }
    moy_lease_lock();
    int t = (int)s_book->chan[ch].timer1 - 1;
    moy_lease_unlock();
    return t;
}

static bool usable_u(int t, uint32_t freq) {
    const tim_t *m = &s_book->tim[t];
    if (freq == MOY_LEDC_FRESH) {
        return m->state == MOY_LEDC_T_FREE;
    }
    return m->state == MOY_LEDC_T_FREE
           || (m->state == MOY_LEDC_T_READY && !m->solo && m->freq == freq);
}

bool moy_ledc_timer_join(int ch, int t, uint32_t freq) {
    if (!chan_ok(ch) || !tim_ok(t)) {
        return false;
    }
    if (moy_ledc_book() == NULL) {
        return false;
    }
    moy_lease_lock();
    bool ok = false;
    if (s_book->chan[ch].holder != 0u) {
        if ((int)s_book->chan[ch].timer1 - 1 == t) {
            ok = freq != MOY_LEDC_FRESH && s_book->tim[t].state == MOY_LEDC_T_READY
                 && s_book->tim[t].freq == freq;
        } else if (freq == MOY_LEDC_FRESH ? s_book->tim[t].state == MOY_LEDC_T_FREE
                                          : (s_book->tim[t].state == MOY_LEDC_T_READY
                                             && !s_book->tim[t].solo && s_book->tim[t].freq == freq)) {
            move_off(ch);
            if (freq == MOY_LEDC_FRESH) {
                tim_free(t);
                s_book->tim[t].state = MOY_LEDC_T_SETTING;
            }
            attach(ch, t);
            ok = true;
        }
    }
    moy_lease_unlock();
    return ok;
}

bool moy_ledc_timer_retune(int t, int ch) {
    if (!chan_ok(ch) || !tim_ok(t)) {
        return false;
    }
    if (moy_ledc_book() == NULL) {
        return false;
    }
    moy_lease_lock();
    tim_t *m = &s_book->tim[t];
    bool ok = (int)s_book->chan[ch].timer1 - 1 == t && m->users == 1u && !m->solo
              && (m->state == MOY_LEDC_T_READY || m->state == MOY_LEDC_T_SETTING);
    if (ok) {
        m->state = MOY_LEDC_T_SETTING;
        m->freq = 0u;
    }
    moy_lease_unlock();
    return ok;
}

void moy_ledc_timer_set(int t, uint32_t freq, uint8_t bits, int32_t clk) {
    if (!tim_ok(t)) {
        return;
    }
    if (moy_ledc_book() == NULL) {
        return;
    }
    moy_lease_lock();
    tim_t *m = &s_book->tim[t];
    if (m->state == MOY_LEDC_T_SETTING) {
        m->state = MOY_LEDC_T_READY;
        m->freq = freq;
        m->bits = bits;
        m->clk = clk;
    }
    moy_lease_unlock();
}

void moy_ledc_timer_closed(int t) {
    if (!tim_ok(t)) {
        return;
    }
    if (moy_ledc_book() == NULL) {
        return;
    }
    moy_lease_lock();
    if (s_book->tim[t].state == MOY_LEDC_T_CLOSING) {
        tim_free(t);
    }
    moy_lease_unlock();
}

bool moy_ledc_timer_usable(int t, uint32_t freq) {
    if (!tim_ok(t)) {
        return false;
    }
    if (moy_ledc_book() == NULL) {
        return false;
    }
    moy_lease_lock();
    bool ok = usable_u(t, freq);
    moy_lease_unlock();
    return ok;
}

int moy_ledc_timer_users(int t, int except) {
    if (!tim_ok(t)) {
        return 0;
    }
    if (moy_ledc_book() == NULL) {
        return 0;
    }
    moy_lease_lock();
    int n = s_book->tim[t].users;
    if (chan_ok(except) && (int)s_book->chan[except].timer1 - 1 == t) {
        n--;
    }
    moy_lease_unlock();
    return n;
}

bool moy_ledc_timer_cfg(int t, uint32_t *freq, uint8_t *bits, int32_t *clk) {
    if (!tim_ok(t)) {
        return false;
    }
    if (moy_ledc_book() == NULL) {
        return false;
    }
    moy_lease_lock();
    const tim_t *m = &s_book->tim[t];
    bool ok = m->state == MOY_LEDC_T_READY;
    if (ok) {
        if (freq != NULL) {
            *freq = m->freq;
        }
        if (bits != NULL) {
            *bits = m->bits;
        }
        if (clk != NULL) {
            *clk = m->clk;
        }
    }
    moy_lease_unlock();
    return ok;
}

int moy_ledc_timer_state(int t) {
    if (!tim_ok(t)) {
        return -1;
    }
    if (moy_ledc_book() == NULL) {
        return -1;
    }
    moy_lease_lock();
    int st = s_book->tim[t].state;
    moy_lease_unlock();
    return st;
}

bool moy_ledc_reserve(int ch, int t, uint32_t holder, uint32_t freq, uint8_t bits,
                      int32_t clk) {
    if (!chan_ok(ch) || !tim_ok(t) || holder == 0u) {
        return false;
    }
    if (moy_ledc_book() == NULL) {
        return false;
    }
    moy_lease_lock();
    bool ok = s_book->chan[ch].holder == 0u && s_book->tim[t].state == MOY_LEDC_T_FREE;
    if (ok) {
        s_book->chan[ch].holder = holder;
        tim_free(t);
        attach(ch, t);
        s_book->tim[t] = (tim_t){ MOY_LEDC_T_READY, 1u, true, bits, freq, clk, 0u };
    }
    moy_lease_unlock();
    return ok;
}

uint32_t moy_ledc_vm_drop(void) {
    if (s_book == NULL) {
        return 0u;              // no book made: nothing the VM holds
    }
    moy_lease_lock();
    uint32_t n = 0;
    for (int ch = 0; ch < MOY_LEDC_CHANNELS; ch++) {
        if (s_book->chan[ch].holder == MOY_LEDC_VM) {
            int t = detach(ch);
            s_book->chan[ch].holder = 0u;
            if (t >= 0) {
                tim_free(t);
            }
            n++;
        }
    }
    for (int t = 0; t < MOY_LEDC_TIMERS; t++) {
        if (s_book->tim[t].state == MOY_LEDC_T_CLOSING && s_book->tim[t].closer == MOY_LEDC_VM) {
            tim_free(t);
        }
    }
    moy_lease_unlock();
    return n;
}

void moy_ledc_reset(void) {
    moy_lease_lock();
    if (s_book != NULL) {
        memset(s_book, 0, sizeof(*s_book));
    }
    moy_lease_unlock();
}

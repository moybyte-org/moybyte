// The touch controllers' protocols, staging and frame side. moy_drivers.h has
// the contract; moy_touch.h the mapping and the no-news contract.
//
// GT911: status 0x814E (bit 7 a fresh buffer, the low nibble the point count),
// point 0x8150, the status cleared after a ready read so the next sample is
// produced. The T-Deck's part lays the point out y(lo,hi) x(lo,hi), the
// Waveshare P4's x then y. On the T-Deck its INT line is counted by an ISR and
// gates the reads (#74): a pass reads on INT activity, while a finger is down,
// on a MOY_TOUCH_SAFETY_MS heartbeat, and always until the line has proved
// itself with a first edge.
//
// GSL3680: RAM-loaded -- after every reset the host streams the board's
// firmware into it (5-byte records: a register, then four bytes, of which a
// page select at 0xF0 sends one), the sequence of the vendor's driver and
// Linux's silead.c. Every read is the chip's current answer: byte 0 of 0x80 is
// the finger count, read alone first so an untouched frame costs one byte;
// then eight bytes with x and y 12-bit (bit 14 of y is a flag, masked).
//
// AXS15231: an 11-byte read command, eight bytes back; [0] != 0 is no report,
// [1] == 0 a release. Untouched, it answers a constant idle filler, which is
// the lift signal its short hold bound stands on.

#include <string.h>

#include "moy_drivers.h"

#define GT911_STATUS 0x814E
#define GT911_POINT 0x8150
#define GSL_TOUCH 0x80
#define GSL_STATUS 0xB0
#define GSL_PAGE 0xF0
#define GSL_POWER 0xE0
#define GSL_CLOCK 0xE4

static const uint8_t AXS_READ[11] = {0xb5, 0xab, 0xa5, 0x5a, 0x00, 0x00, 0x00, 0x08, 0x00, 0x00, 0x00};

void moy_touchdev_init(moy_touchdev_t *d, uint8_t kind, uint8_t addr, const moy_touch_map_t *map,
                       bool extrapolate, float damp, int32_t hold_ms) {
    memset(d, 0, sizeof(*d));
    MOY_DRV_LOCK_INIT(&d->lock);
    d->kind = kind;
    d->addr = addr;
    d->map = *map;
    d->fb_status = -1;
    moy_touch_held_init(&d->held, extrapolate, map->w, map->h, damp, hold_ms);
}

static int reg16_read(const moy_i2c_t *bus, uint8_t addr, uint16_t reg, uint8_t *dst, size_t n) {
    uint8_t r[2] = {(uint8_t)(reg >> 8), (uint8_t)reg};
    return bus->write_read(bus->ctx, addr, r, 2, dst, n);
}

static int reg16_write(const moy_i2c_t *bus, uint8_t addr, uint16_t reg, uint8_t v) {
    uint8_t w[3] = {(uint8_t)(reg >> 8), (uint8_t)reg, v};
    return bus->write(bus->ctx, addr, w, 3);
}

static int reg8_write(const moy_i2c_t *bus, uint8_t reg, const uint8_t *src, size_t n) {
    uint8_t w[5];
    w[0] = reg;
    memcpy(&w[1], src, n);
    return bus->write(bus->ctx, 0x40, w, n + 1);
}

static int axs_read(const moy_touchdev_t *d, const moy_i2c_t *bus, uint8_t out[8]) {
    if (bus->write(bus->ctx, d->addr, AXS_READ, sizeof(AXS_READ)) != 0) {
        return -1;
    }
    return bus->read(bus->ctx, d->addr, out, 8);
}

void moy_touchdev_probe(moy_touchdev_t *d, const moy_i2c_t *bus) {
    uint8_t b[8];
    switch (d->kind) {
        case MOY_TOUCH_GT911: {
            static const uint8_t ADDRS[2] = {0x5D, 0x14};
            for (int i = 0; i < 2 && !d->available; i++) {
                if (d->addr == 0 || d->addr == ADDRS[i]) {
                    if (reg16_read(bus, ADDRS[i], GT911_STATUS, b, 1) == 0) {
                        d->addr = ADDRS[i];
                        d->available = true;
                    }
                }
            }
            break;
        }
        case MOY_TOUCH_AXS:
            for (int i = 0; i < 3 && !d->available; i++) {
                if (axs_read(d, bus, b) == 0) {
                    d->available = true;
                } else if (i < 2) {
                    bus->sleep_ms(bus->ctx, 50);
                }
            }
            break;
        case MOY_TOUCH_GSL3680:
            d->init_state = d->fw != NULL ? 1 : -1;
            break;
    }
    d->last_read_ms = bus->ms(bus->ctx);
}

// -- the GSL3680's bring-up --------------------------------------------------------

static void gsl_w(const moy_i2c_t *bus, uint8_t reg, uint8_t v) {
    reg8_write(bus, reg, &v, 1);
}

static void gsl_w32(const moy_i2c_t *bus, uint8_t reg, uint32_t v) {
    uint8_t b[4] = {(uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16), (uint8_t)(v >> 24)};
    reg8_write(bus, reg, b, 4);
}

static void gsl_reset_chip(const moy_i2c_t *bus, void (*reset)(void *, bool), void *ctx) {
    reset(ctx, true);
    gsl_w(bus, GSL_POWER, 0x88);
    bus->sleep_ms(bus->ctx, 10);
    gsl_w(bus, GSL_CLOCK, 0x04);
    bus->sleep_ms(bus->ctx, 10);
    gsl_w32(bus, 0xBC, 0);
    bus->sleep_ms(bus->ctx, 10);
}

static void gsl_startup(const moy_i2c_t *bus) {
    gsl_w(bus, GSL_POWER, 0x00);
    bus->sleep_ms(bus->ctx, 10);
}

bool moy_gsl_init(moy_touchdev_t *d, const moy_i2c_t *bus,
                  void (*reset)(void *ctx, bool before), void *reset_ctx) {
    // clear_reg
    gsl_w(bus, GSL_POWER, 0x88);
    bus->sleep_ms(bus->ctx, 20);
    gsl_w(bus, 0x88, 0x01);
    bus->sleep_ms(bus->ctx, 5);
    gsl_w(bus, GSL_CLOCK, 0x04);
    bus->sleep_ms(bus->ctx, 5);
    gsl_w(bus, GSL_POWER, 0x00);
    bus->sleep_ms(bus->ctx, 20);
    gsl_reset_chip(bus, reset, reset_ctx);
    size_t n = d->fw_len / 5;
    for (size_t i = 0; i < n; i++) {
        const uint8_t *r = &d->fw[i * 5];
        reg8_write(bus, r[0], &r[1], r[0] == GSL_PAGE ? 1 : 4);
    }
    d->loaded = (uint32_t)n;
    gsl_startup(bus);
    gsl_reset_chip(bus, reset, reset_ctx);
    gsl_startup(bus);
    reset(reset_ctx, false);
    bus->sleep_ms(bus->ctx, 30);
    uint8_t reg = GSL_STATUS;
    uint8_t st[4];
    bool alive = bus->write_read(bus->ctx, 0x40, &reg, 1, st, 4) == 0
                 && st[0] == 0x5A && st[1] == 0x5A && st[2] == 0x5A && st[3] == 0x5A;
    d->init_state = alive ? 2 : -1;
    d->available = alive;
    return alive;
}

// -- a pass ------------------------------------------------------------------------

static void stat(moy_touchdev_t *d, const moy_i2c_t *bus, uint32_t t0, uint8_t phase, int status) {
    uint32_t el = bus->us(bus->ctx) - t0;
    d->stat_n++;
    if (el > d->stat_max_us) {
        d->stat_max_us = el;
    }
    if (el >= 5000) {
        d->stat_over5++;
        if (el >= 20000) {
            d->stat_over20++;
        }
    }
    if (el >= 200000 && !d->fb_set) {
        d->fb_set = true;
        d->fb_ms = bus->ms(bus->ctx);
        d->fb_phase = phase;
        d->fb_status = (int16_t)status;
        d->fb_n = d->stat_n;
    }
}

static bool should_read(moy_touchdev_t *d, const moy_i2c_t *bus) {
    if (!d->gate) {
        return true;
    }
    uint32_t n = d->int_count;
    if (n != d->int_last) {
        d->int_last = n;
        d->int_seen = true;
        return true;
    }
    if (!d->int_seen || d->touching) {
        return true;
    }
    if (moy_input_ticks_diff(bus->ms(bus->ctx), d->last_read_ms) >= MOY_TOUCH_SAFETY_MS) {
        return true;
    }
    d->stat_skipped++;
    return false;
}

static int gt911_read(moy_touchdev_t *d, const moy_i2c_t *bus, int32_t *rx, int32_t *ry) {
    uint32_t t0 = bus->us(bus->ctx);
    uint8_t st;
    if (reg16_read(bus, d->addr, GT911_STATUS, &st, 1) != 0) {
        stat(d, bus, t0, 0, -1);
        return MOY_RAW_NONE;
    }
    if (!(st & 0x80)) {
        stat(d, bus, t0, 0, st);
        return MOY_RAW_NONE;
    }
    uint32_t t1 = bus->us(bus->ctx);
    int raw = MOY_RAW_UP;
    uint8_t p[4];
    if (d->clear_first) {
        reg16_write(bus, d->addr, GT911_STATUS, 0);
    }
    uint32_t t2 = bus->us(bus->ctx);
    if ((st & 0x0F) >= 1) {
        if (reg16_read(bus, d->addr, GT911_POINT, p, 4) == 0) {
            raw = MOY_RAW_POINT;
            if (d->yx) {
                *rx = p[2] | (p[3] << 8);
                *ry = p[0] | (p[1] << 8);
            } else {
                *rx = p[0] | (p[1] << 8);
                *ry = p[2] | (p[3] << 8);
            }
        } else {
            raw = MOY_RAW_NONE;
        }
    }
    uint32_t t3 = bus->us(bus->ctx);
    if (!d->clear_first) {
        reg16_write(bus, d->addr, GT911_STATUS, 0);
    }
    uint32_t t4 = bus->us(bus->ctx);
    uint32_t sp = t1 - t0, pp = t3 - t2, cp = (t4 - t3) + (t2 - t1);
    uint8_t phase = 0;
    if (pp > sp) {
        phase = 1;
        sp = pp;
    }
    if (cp > sp) {
        phase = 2;
    }
    stat(d, bus, t0, phase, st);
    return raw;
}

static int gsl_read(moy_touchdev_t *d, const moy_i2c_t *bus, int32_t *rx, int32_t *ry, uint8_t *n) {
    uint32_t t0 = bus->us(bus->ctx);
    uint8_t reg = GSL_TOUCH;
    uint8_t b[8];
    if (bus->write_read(bus->ctx, 0x40, &reg, 1, b, 1) != 0) {
        stat(d, bus, t0, 0, -1);
        return MOY_RAW_NONE;
    }
    if (b[0] == 0) {
        stat(d, bus, t0, 0, 0);
        *n = 0;
        return MOY_RAW_UP;
    }
    if (bus->write_read(bus->ctx, 0x40, &reg, 1, b, 8) != 0) {
        stat(d, bus, t0, 1, -1);
        return MOY_RAW_NONE;
    }
    stat(d, bus, t0, 1, b[0]);
    *n = b[0];
    if (b[0] < 1) {
        return MOY_RAW_UP;
    }
    *rx = ((b[7] & 0x0F) << 8) | b[6];
    *ry = ((b[5] & 0x0F) << 8) | b[4];
    return MOY_RAW_POINT;
}

static int axs_pass_read(moy_touchdev_t *d, const moy_i2c_t *bus, int32_t *rx, int32_t *ry) {
    uint32_t t0 = bus->us(bus->ctx);
    uint8_t b[8];
    int rc = axs_read(d, bus, b);
    stat(d, bus, t0, 0, rc == 0 ? b[0] : -1);
    if (rc != 0 || b[0] != 0) {
        return MOY_RAW_NONE;
    }
    if (b[1] == 0) {
        return MOY_RAW_UP;
    }
    *rx = ((b[2] & 0x0F) << 8) | b[3];
    *ry = ((b[4] & 0x0F) << 8) | b[5];
    return MOY_RAW_POINT;
}

void moy_touchdev_pass(moy_touchdev_t *d, const moy_i2c_t *bus) {
    if (!d->available) {
        if (d->kind == MOY_TOUCH_AXS && ++d->reprobe >= MOY_TOUCH_REPROBE) {
            d->reprobe = 0;
            moy_touchdev_probe(d, bus);
        }
        return;
    }
    if (!should_read(d, bus)) {
        return;
    }
    d->last_read_ms = bus->ms(bus->ctx);
    int32_t x = 0, y = 0;
    uint8_t n = 1;
    int raw;
    switch (d->kind) {
        case MOY_TOUCH_GT911:
            raw = gt911_read(d, bus, &x, &y);
            break;
        case MOY_TOUCH_GSL3680:
            raw = gsl_read(d, bus, &x, &y, &n);
            break;
        default:
            raw = axs_pass_read(d, bus, &x, &y);
            break;
    }
    if (raw == MOY_RAW_UP) {
        d->touching = false;
    } else if (raw == MOY_RAW_POINT) {
        d->touching = true;
    }
    MOY_DRV_LOCK(&d->lock);
    if (raw == MOY_RAW_UP) {
        d->st_up = true;
        d->st_fingers = 0;
    } else if (raw == MOY_RAW_POINT) {
        d->st_point = true;
        d->st_x = x;
        d->st_y = y;
        d->st_fingers = n;
    }
    MOY_DRV_UNLOCK(&d->lock);
}

// -- the frame ---------------------------------------------------------------------

void moy_touchdev_poll(moy_touchdev_t *d, uint32_t now, moy_touch_pt_t *out) {
    if (!d->available) {
        d->held.fresh = true;
        memset(out, 0, sizeof(*out));
        return;
    }
    int raw = MOY_RAW_NONE;
    int32_t x = 0, y = 0;
    MOY_DRV_LOCK(&d->lock);
    if (d->st_point) {
        d->st_point = false;
        raw = MOY_RAW_POINT;
        x = d->st_x;
        y = d->st_y;
        d->fingers = d->st_fingers;
    } else if (d->st_up) {
        d->st_up = false;
        raw = MOY_RAW_UP;
        d->fingers = 0;
    }
    MOY_DRV_UNLOCK(&d->lock);
    if (raw == MOY_RAW_UP) {
        moy_touch_release(&d->held, out);
    } else if (raw == MOY_RAW_POINT) {
        d->raw_x = x;
        d->raw_y = y;
        d->has_raw = true;
        int32_t mx, my;
        moy_touch_map(&d->map, x, y, &mx, &my);
        moy_touch_sample(&d->held, mx, my, now, out);
    } else {
        moy_touch_hold(&d->held, now, out);
    }
}

void moy_ball_take(moy_ball_t *b, uint32_t out[4]) {
    for (int i = 0; i < 4; i++) {
        out[i] = __atomic_exchange_n(&b->counts[i], 0u, __ATOMIC_RELAXED);
    }
}

size_t moy_touchdev_sizeof(void) {
    return sizeof(moy_touchdev_t);
}

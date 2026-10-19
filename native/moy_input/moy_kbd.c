// The T-Deck keyboard's driver. moy_drivers.h has the contract.
//
// THE MATRIX, from the vendor keyboard firmware (examples/Keyboard_ESP32C3's
// `keyboard[col][row]`): five columns, streamed as bytes d0..d4, one bit per row.
//
//   d0: q  w  sym  a   ALT  space  Mic
//   d1: e  s  d    p   x    z      LShift
//   d2: r  g  t    RSh v    c      f
//   d3: u  h  y    Ent b    n      j
//   d4: o  l  i    Bsp $    m      k
//        ^0 ^1 ^2  ^3  ^4   ^5     ^6      <- bit
//
// THE SCHEME (owner call, 2026-08-14): the left thumb steers (W A S D), the
// right fires (L is A, K is B). SPACE is the one alias of A, because space =
// jump is a habit a kid already has; ENTER is `run` and nothing else is; ESC
// is `stop` (ASCII path only). Every other key is a plain letter for
// key()/keyp(), BACKSPACE (0x08) among them.
//
// THE CONSOLE KEY (owner call, 2026-10-18) is Mic: `start`, the Super of every
// other device, in every input mode and never a cart's. In the raw matrix it
// is d0 bit 6. In ASCII mode the C3 sends NOTHING for a bare Mic (its table
// entry is NULL, so the press reads as the idle byte 0x00); Shift+Mic is the
// one ASCII spelling it has, the byte 0xE0 (NULL - 32), and Sym+Mic is '0'.
// A text surface suppresses every alias before this table is consulted, and
// `start` is not an alias.

#include <string.h>

#include "moy_drivers.h"

enum {
    B_LEFT = 1u << 0, B_RIGHT = 1u << 1, B_UP = 1u << 2, B_DOWN = 1u << 3,
    B_A = 1u << 4, B_B = 1u << 5, B_RUN = 1u << 6, B_STOP = 1u << 10,
    B_START = 1u << 14,
};

#define KBD_SHIFT_MIC 0xE0      // ASCII mode's Shift+Mic
#define KBD_MIC_BYTE 0
#define KBD_MIC_BIT 0x40        // raw d0

uint32_t moy_kbd_buttons_for_key(int32_t key) {
    if (key >= 'A' && key <= 'Z') {
        key += 32;
    }
    switch (key) {
        case 'w': return B_UP;
        case 's': return B_DOWN;
        case 'a': return B_LEFT;
        case 'd': return B_RIGHT;
        case 'l': return B_A;
        case ' ': return B_A;
        case 'k': return B_B;
        case 0x0D: return B_RUN;
        case 0x1B: return B_STOP;
        case KBD_SHIFT_MIC: return B_START;
        default: return 0;
    }
}

// Every key of the matrix the console decodes, as (byte, bit, key, sym): the
// byte the C3 types for it, and the one it types with Sym held (0: the plain
// key's). The matrix carries the whole keyboard, so a raw-mode surface reads
// the letters, digits and symbols an ASCII-mode one does: the home's verb
// letters (C N Y X F, launcher_layer._VERB_KEY), Search's '/' (Sym+G) and a
// cart's key(). Tested in this order: the last match wins the key slot, so
// BACKSPACE is last and beats any letter held with it. Mic is not here: it is
// a button and no key.
static const uint8_t RAW_KEYS[][4] = {
    {0, 0x02, 'w', '1'}, {1, 0x02, 's', '4'},
    {0, 0x08, 'a', '*'}, {1, 0x04, 'd', '5'},
    {4, 0x02, 'l', '"'}, {4, 0x40, 'k', '\''},
    {2, 0x01, 'r', '3'},
    {0, 0x01, 'q', '#'}, {1, 0x01, 'e', '2'},
    {3, 0x02, 'h', ':'}, {3, 0x40, 'j', ';'},
    {1, 0x20, 'z', '7'}, {1, 0x10, 'x', '8'},
    {1, 0x08, 'p', '@'},
    {2, 0x02, 'g', '/'}, {2, 0x04, 't', '('}, {2, 0x10, 'v', '?'},
    {2, 0x20, 'c', '9'}, {2, 0x40, 'f', '6'},
    {3, 0x01, 'u', '_'}, {3, 0x04, 'y', ')'}, {3, 0x10, 'b', '!'},
    {3, 0x20, 'n', ','},
    {4, 0x01, 'o', '+'}, {4, 0x04, 'i', '-'}, {4, 0x10, '$', 0},
    {4, 0x20, 'm', '.'},
    {0, 0x20, ' ', 0}, {3, 0x08, 0x0D, 0},
    {4, 0x08, 0x08, 0},
};

#define KBD_SYM_BYTE 0
#define KBD_SYM_BIT 0x04        // raw d0, the Sym key
#define KBD_LSHIFT_BYTE 1
#define KBD_LSHIFT_BIT 0x40
#define KBD_RSHIFT_BYTE 2
#define KBD_RSHIFT_BIT 0x08

#define N_RAW_KEYS (sizeof(RAW_KEYS) / sizeof(RAW_KEYS[0]))

// The byte matrix key `i` types in frame `d`: its Sym symbol with Sym held, a
// letter's capital with a Shift held, as the C3's ASCII mode types them.
static int32_t key_byte(const uint8_t d[5], size_t i) {
    bool sym = (d[KBD_SYM_BYTE] & KBD_SYM_BIT) != 0;
    bool shift = (d[KBD_LSHIFT_BYTE] & KBD_LSHIFT_BIT) || (d[KBD_RSHIFT_BYTE] & KBD_RSHIFT_BIT);
    int32_t c = sym && RAW_KEYS[i][3] ? RAW_KEYS[i][3] : RAW_KEYS[i][2];
    if (shift && c >= 'a' && c <= 'z') {
        c -= 32;                    // the button is the same
    }
    return c;
}

void moy_kbd_decode_raw(const uint8_t d[5], uint32_t *held, int32_t *key) {
    uint32_t h = 0;
    int32_t k = 0;
    for (size_t i = 0; i < N_RAW_KEYS; i++) {
        if (d[RAW_KEYS[i][0]] & RAW_KEYS[i][1]) {
            int32_t c = key_byte(d, i);
            k = c;
            h |= moy_kbd_buttons_for_key(c);
        }
    }
    if (d[KBD_MIC_BYTE] & KBD_MIC_BIT) {
        h |= B_START;
    }
    *held = h;
    *key = k;
}

void moy_kbd_init(moy_kbd_t *k, const moy_i2c_t *bus, moy_input_t *table, uint32_t src) {
    memset(k, 0, sizeof(*k));
    k->table = table;
    k->src = src;
    k->raw_game = true;
    k->want = -1;
    uint8_t b;
    k->available = bus->read(bus->ctx, MOY_KBD_ADDR, &b, 1) == 0;
}

void moy_kbd_game_mode(moy_kbd_t *k, bool on) {
    k->want = on ? 1 : 0;
}

// One read, timed (a 5-byte read at 400 kHz is ~135 us; milliseconds are the
// C3 clock-stretching). A failed read is one stale frame, not a dead keyboard;
// only MOY_KBD_ERR_RUN of them in a row end the session.
static bool timed_read(moy_kbd_t *k, const moy_i2c_t *bus, uint8_t *dst, size_t n) {
    uint32_t t0 = bus->us(bus->ctx);
    if (bus->read(bus->ctx, MOY_KBD_ADDR, dst, n) != 0) {
        k->stat_timeouts++;
        if (++k->err_run >= MOY_KBD_ERR_RUN) {
            k->available = false;
        }
        return false;
    }
    k->err_run = 0;
    uint32_t el = bus->us(bus->ctx) - t0;
    k->stat_n++;
    if (el > k->stat_max_us) {
        k->stat_max_us = el;
        k->stat_max_raw = k->raw_mode;
    }
    if (el >= 5000) {
        k->stat_over5++;
        if (el >= 20000) {
            k->stat_over20++;
        }
    }
    return true;
}

static int32_t read_key(moy_kbd_t *k, const moy_i2c_t *bus) {
    uint8_t b;
    if (!k->available || !timed_read(k, bus, &b, 1)) {
        return 0;
    }
    return b;
}

static void set_mode(moy_kbd_t *k, const moy_i2c_t *bus, bool want) {
    if (want == k->raw_mode) {
        return;
    }
    if (want) {
        static const uint8_t RAW = 0x03;
        k->raw_mode = bus->write(bus->ctx, MOY_KBD_ADDR, &RAW, 1) == 0;
        k->raw_valid = false;
        return;
    }
    static const uint8_t KEY = 0x04;
    bus->write(bus->ctx, MOY_KBD_ADDR, &KEY, 1);
    k->raw_mode = false;
    k->held = 0;
    // The switch drains its own byte: what the C3 holds now was typed while
    // the matrix streamed, before the text surface that asked for ASCII
    // existed, and delivered it is a letter the kid did not type.
    for (int i = 0; i < MOY_KBD_DRAIN; i++) {
        if (read_key(k, bus) == 0) {
            break;
        }
    }
    k->held_until = 0;
}

// A text surface's typing from the matrix: each key down in `d` and up in
// `was` is one typed byte, queued as the C3's ASCII mode queues it (once a
// press, no autorepeat).
static void type_edges(moy_kbd_t *k, const uint8_t was[5], const uint8_t d[5]) {
    for (size_t i = 0; i < N_RAW_KEYS; i++) {
        uint8_t at = RAW_KEYS[i][0];
        uint8_t bit = RAW_KEYS[i][1];
        if ((d[at] & bit) && !(was[at] & bit)) {
            moy_input_key(k->table, k->src, key_byte(d, i));
        }
    }
}

// What a surface takes of `mask`: a text surface none of the aliases, and the
// console key always.
static uint32_t text_mask(const moy_kbd_t *k, uint32_t mask) {
    return moy_input_text_mode(k->table) ? mask & B_START : mask;
}

void moy_kbd_pass(moy_kbd_t *k, const moy_i2c_t *bus) {
    if (!k->available) {
        return;
    }
    if (k->want >= 0) {
        k->want = -1;
        // Every surface reads the matrix, a text one included: it types each
        // key's press edge, and it sees Mic, which ASCII cannot. ASCII is for
        // a keyboard firmware that ignores 0x03, or RAW_GAME_MODE off.
        set_mode(k, bus, k->raw_game && !k->raw_unsupported);
    }
    uint32_t now = bus->ms(bus->ctx);
    if (k->raw_mode) {
        bool text = moy_input_text_mode(k->table);
        if (text != k->raw_text) {
            // a text surface takes typed bytes, a game the held key: neither
            // inherits the other's key
            k->raw_text = text;
            moy_input_set_key(k->table, k->src, 0);
        }
        uint8_t d[5];
        if (timed_read(k, bus, d, 5)) {
            if (!k->raw_valid || memcmp(d, k->raw_bytes, 5) != 0) {
                // Only a byte no sane chord makes: d0 >= 0x61 is Space and Mic
                // with a letter key, three keys at once, where Mic with one
                // key is an uppercase letter's code (Mic+Q is 'A').
                uint32_t stray = (d[1] | d[2] | d[3] | d[4]) == 0 && d[0] >= 0x61
                                 ? moy_kbd_buttons_for_key(d[0]) : 0;
                if (stray) {
                    // A printable byte while we asked for the matrix: the
                    // firmware ignored 0x03. The session stays on ASCII.
                    k->raw_mode = false;
                    k->raw_unsupported = true;
                    k->held = stray;
                    k->held_until = now + MOY_KBD_HOLD_MS;
                    if (!(stray & B_START)) {
                        moy_input_key(k->table, k->src, d[0]);
                    }
                    moy_input_set_mask(k->table, k->src, text_mask(k, stray));
                    return;
                }
                if (text && k->raw_valid) {
                    type_edges(k, k->raw_bytes, d);
                }
                moy_kbd_decode_raw(d, &k->raw_held, &k->raw_key);
                memcpy(k->raw_bytes, d, 5);
                k->raw_valid = true;
            }
        }
        // A failed read holds the last matrix for this frame: an empty one
        // would release and re-press a held button, which btnp() reads as a
        // second press.
        if (!text) {
            moy_input_set_key(k->table, k->src, k->raw_key);
        }
        moy_input_set_mask(k->table, k->src, text_mask(k, k->raw_held));
        return;
    }
    int32_t key = read_key(k, bus);
    if (key != 0) {
        k->held = moy_kbd_buttons_for_key(key);
        k->held_until = now + MOY_KBD_HOLD_MS;
        if (!(k->held & B_START)) {
            moy_input_key(k->table, k->src, key);   // the console key is no typed key
        }
    } else if (moy_input_ticks_diff(k->held_until, now) <= 0) {
        k->held = 0;
    }
    if (moy_input_text_mode(k->table)) {
        k->held &= B_START;
    }
    moy_input_set_mask(k->table, k->src, k->held);
}

size_t moy_kbd_sizeof(void) {
    return sizeof(moy_kbd_t);
}

// The matrix table, entry by entry, for the host's tests: 0 past the end.
int moy_kbd_raw_key(size_t i, uint8_t *byte, uint8_t *bit, uint8_t *key, uint8_t *sym) {
    if (i >= sizeof(RAW_KEYS) / sizeof(RAW_KEYS[0])) {
        return 0;
    }
    *byte = RAW_KEYS[i][0];
    *bit = RAW_KEYS[i][1];
    *key = RAW_KEYS[i][2];
    *sym = RAW_KEYS[i][3];
    return 1;
}

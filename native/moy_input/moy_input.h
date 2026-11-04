// moy_input: the merged input table every surface reads, its sources and the
// pointer (docs/history/kernel_survival_2026-10.md section 4.1).
//
// One table body for every tier: the boards' kernel table (moy_input_kernel),
// the tables the MicroPython binding (modmoy_input.c) and the host's ctypes
// binding (runtime/host/moy_input.py) make, and the fuzzer's.
//
// THE BUTTONS are libmoy's moy_button order, then `home` and the console's own
// six: bit i of every mask is MOY_INPUT_NAMES[i]. A table holds the first
// `nbuttons` of them (the boards all fifteen, the host and the browser eight);
// a button outside a table's vocabulary is refused (MOY_INPUT_BAD).
//
// THE SOURCES. Every producer writes its own source and only that: its held
// bits, its key, its one-shot keys. A source write lands in the source's LATCH,
// which is internal RAM on a board and written under the table's spinlock, so
// an ISR, the poller task and NimBLE's host task may write it while the cache is
// off. moy_input_begin_frame is the merge's one author: it takes the latches
// once per frame, unions them into the table (PSRAM on a board), and computes
// the edges. A read between a write and the next begin_frame answers for the
// frame still current.
//
// THE KEY. A source's key is a level (the byte it holds now) and the table's
// last_key follows the source that typed most recently: a new nonzero value
// takes ownership, the owner re-asserts on every write, and only the owner
// going quiet hands the slot to another source still holding a key. It moves
// at the write, not at begin_frame. moy_input_key is the one-shot form: the
// key is queued and delivered as the source's level for exactly one frame, a
// zero frame between two equal keys so an edge reader sees both.
//
// A KEY EVENT is the key and the modifiers held with it, one int32: the code
// in the low 16 bits, MOY_INPUT_MOD_* above MOY_INPUT_MODS_AT. The code is the
// byte the key types with Shift applied and Ctrl not ('z' for Ctrl+Z, '!'
// for Shift+1), or a MOY_INPUT_KEY_* above the bytes for a key that types
// none. A source's key, its queue and last_key hold events; a byte with no
// modifiers is the event of a key typed alone, so a producer that knows none
// writes bytes. moy_input_typed is what an event types: Ctrl and a letter the
// control byte (Ctrl+Z 0x1A), any other Ctrl, Alt or Super chord nothing, so
// a shortcut reads the event and a text surface reads the byte.
//
// THE MODIFIERS a source holds are a level of their own (moy_input_set_mods),
// so a button pressed with Ctrl held reads it too: moy_input_mods is the
// frame's key event's modifiers and every source's held ones.
//
// THE WHEEL and THE POINTER'S BUTTONS. A source adds its wheel's notches
// (moy_input_scroll: across and down, positive right and down) and holds its
// pointer's buttons as a level (MOY_INPUT_PB_*: the primary is also the
// sample's `down`); the merge sums the notches into the frame's and takes
// the buttons' press edges (one pressed and let go between two merges is held
// for one frame). A mouse beside a keyboard -- the host's, the
// browser's, a BLE mouse -- writes them on its own source.
//
// CART KEYS. While a game owns the keyboard (the console says, every frame)
// a desk keyboard's letters are the game's buttons too (WASD the d-pad, Z X
// and Space A and B); on every other surface they are keys and nothing else.
// The T-Deck's keyboard reads it the other way: off a game and a text
// surface its W A S D are the directions and no key (moy_kbd.c).
//
// THE POINTER. A source may also hold a pointer sample -- a place, down, the
// press edge and whether the hardware measured it this frame -- written like
// its buttons. The frame's sample is the merge: the newest sample of a source
// that is down, else the newest of any; an edge is delivered once. The touch
// driver and the dev channel's gestures are two such sources, so a scripted
// tap reaches the pointer the way a finger does.
//
// REPEAT. A held button repeats at the merge, per source and per button, on
// the clock begin_frame is given: after the table's repeat delay of level,
// one repeat every repeat period, and a frame that took two periods owes two.
// The frame's count for a button is the most any source owes it
// (moy_input_repeats; moy_input_rep_word packs the four directions). A
// source's TYPING LEVEL (moy_input_set_typing: the key a keyboard holds down
// for typing, which no cart reads) repeats the same way into
// moy_input_key_repeats; a chord (Ctrl, Alt, Super) and a key that types no
// byte do not repeat. Repeats are counts beside the frame, never edges: they
// enter no pressed mask, no key event and no last_key, so a cart's btnp,
// key() and keyp() read what they read without them. moy_input_add_steps adds
// a producer's steps (the trackball's) to the frame's counts after the merge.
// moy_input_spend_edges zeroes the frame's edges and counts, a held button
// keeping its level and its schedule (a caret took the step);
// moy_input_clear_edges also starts every held repeat's delay over. A clock
// of 0 at every merge repeats nothing.
//
// PLAYERS. A source sits on a player slot (0 .. MOY_INPUT_PLAYERS-1). The
// union (MOY_INPUT_UNION) is every source; a player's view is its sources'.
// While every source sits on one slot, that slot's view is the union and
// every other slot's is empty.
//
// THE EVENTS (moy_event.h; docs/os_2026-11/hardware.md section 2). Every
// source has a ring of event records beside its latch, written by the same
// call under the same lock: a held bit that changes is a BTN record, a key
// level that changes a KEY_UP for the old and a KEY_DOWN (and TEXT, when it
// types) for the new, a one-shot key all three at once, a pointer sample that
// goes down, moves or lifts a TOUCH record. A write that changes nothing
// writes no record, so a poller re-asserting its level each frame costs none.
// The merge reads the latches; a reader reads the records. A ring is made with
// the source, MOY_EV_DEPTH deep and stamped POLL; moy_input_source_events
// names its provenance and depth (an ISR's ring is internal RAM, any other the
// table's). A full ring drops its oldest record, counted against the source
// (moy_input_ring_info) when a reader of its kind had not read it, and against
// that reader, which reads a LOST record where the gap was. A reader is a
// cursor per source and a kind mask; it starts at the records written after it.
//
// RING SOURCES (hardware.md section 2): a source a lease or a device API
// brings is its ring alone, no latch: made with moy_input_ring_open, ended
// with moy_input_ring_close (its handle then stale), MOY_INPUT_RING_SOURCES of
// them beside the latch sources, read by the same readers. A ring a task
// writes (FIFO, POLL) is written with moy_input_post and taken from THE
// SHARES' RINGS row (native/moy_play/moy_play.h) like every task's ring; an
// ISR's is internal RAM, under the kernel's internal cap, and is written only
// through a moy_input_isr_t (moy_input_isr_put), which touches nothing but the
// ring, its lock and the clock, so it is safe from an IRAM ISR where its text
// is IRAM (MOY_INPUT_ISR_IRAM) and from any other ISR anywhere. Its drops are
// counted where they are seen: a reader whose cursor the writer passed reads
// a LOST record for every record it missed, whatever their kind, when it
// reads any kind the ring has held, and the source counts each such record
// once.
//
// A handle is the spine's: gen << 12 | MOY_KIND_SRC << 8 | slot. A latch
// source's generation is 1 (it is never released), and slot 0 is the table's
// own source, "local". A ring source's slot follows the latch slots and its
// generation moves when it closes. A record's `src` is gen << 8 | slot.

#ifndef MOY_INPUT_H
#define MOY_INPUT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "moy_event.h"

// ESP_PLATFORM is not defined for a usermod's sources on the esp32 port, so a
// board is recognised by the header it has.
#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#define MOY_INPUT_BOARD 1
#endif
#endif

#define MOY_INPUT_BUTTONS 16u
#define MOY_INPUT_HOST_BUTTONS 8u
#define MOY_INPUT_SOURCES 8u
#define MOY_INPUT_RING_SOURCES 8u   // sources that are a ring alone
#define MOY_INPUT_RINGS (MOY_INPUT_SOURCES + MOY_INPUT_RING_SOURCES)
#define MOY_INPUT_PLAYERS 8u
#define MOY_INPUT_UNION 0xFFu
#define MOY_INPUT_KEYQ 8u           // one-shot keys a source queues
#define MOY_INPUT_NAME 15u          // a source name's bytes

#define MOY_INPUT_CURSOR_IDLE_MS 2000

// The repeat's parameters and the trackball's coast; a board's board.toml
// [input] overrides them as compile definitions (tools/board_config.py).
#ifndef MOY_INPUT_REPEAT_DELAY_MS
#define MOY_INPUT_REPEAT_DELAY_MS 400
#endif
#ifndef MOY_INPUT_REPEAT_RATE_MS
#define MOY_INPUT_REPEAT_RATE_MS 50
#endif
// Steps a trackball roll delivers above MOY_INPUT_BALL_COAST_RATE a second
// become a coast instead: MOY_INPUT_BALL_COAST thousandths of a step a ms per
// surplus step, delivered on as steps that decay as a fling does
// (runtime/shell/console.py Workstation._ball_steps); 0 is no momentum.
#ifndef MOY_INPUT_BALL_COAST
#define MOY_INPUT_BALL_COAST 0
#endif
#ifndef MOY_INPUT_BALL_COAST_RATE
#define MOY_INPUT_BALL_COAST_RATE 20
#endif
// The ISR writer's text (moy_input_isr_put, moy_edge's ISR): IRAM where a
// board's board.toml [input] isr_iram says so (both P4s), so an ISR source
// stays hot through a collection; elsewhere flash, which costs an idle board
// no internal RAM and serves an ISR installed without ESP_INTR_FLAG_IRAM (the
// cache-off window masks it). An IRAM ISR calls it only where it is IRAM.
#ifndef MOY_INPUT_ISR_IRAM
#define MOY_INPUT_ISR_IRAM 0
#endif
#if defined(MOY_INPUT_BOARD) && MOY_INPUT_ISR_IRAM
#include "esp_attr.h"
#define MOY_INPUT_ISR_TEXT IRAM_ATTR
#else
#define MOY_INPUT_ISR_TEXT
#endif
#define MOY_INPUT_REP_MAX 255u      // a frame's count for one button, saturated
#define MOY_INPUT_POINTER_LINGER_MS 1500

enum {
    MOY_INPUT_OK = 0,
    MOY_INPUT_STALE = 1,    // the handle names no source of this table
    MOY_INPUT_FULL = 2,     // every source slot is taken
    MOY_INPUT_NOMEM = 3,
    MOY_INPUT_BAD = 4,      // a button outside the vocabulary, a player out of range
};

// The pointer's flags as a cart reads them (moy_input_ptr_state).
enum {
    MOY_INPUT_P_NONE = 0,
    MOY_INPUT_P_LIVE = 1,
    MOY_INPUT_P_HELD = 2,
    MOY_INPUT_P_CLICK = 4,
};

#define MOY_INPUT_MODS_AT 16
enum {
    MOY_INPUT_MOD_CTRL = 1,
    MOY_INPUT_MOD_SHIFT = 2,
    MOY_INPUT_MOD_ALT = 4,
    MOY_INPUT_MOD_SUPER = 8,
};
enum {
    MOY_INPUT_PB_PRIMARY = 1,
    MOY_INPUT_PB_MIDDLE = 2,
    MOY_INPUT_PB_SECONDARY = 4,
};
// The keys that type no byte.
enum {
    MOY_INPUT_KEY_F1 = 0x101,       // ... MOY_INPUT_KEY_F1 + 11 is F12
    MOY_INPUT_KEY_LEFT = 0x110,
    MOY_INPUT_KEY_RIGHT,
    MOY_INPUT_KEY_UP,
    MOY_INPUT_KEY_DOWN,
    MOY_INPUT_KEY_PGUP,
    MOY_INPUT_KEY_PGDN,
};
#define MOY_INPUT_EVENT(code, mods) ((int32_t)(code) | ((int32_t)(mods) << MOY_INPUT_MODS_AT))
#define MOY_INPUT_CODE(ev) ((int32_t)(ev) & 0xFFFF)
#define MOY_INPUT_MODS(ev) ((uint8_t)(((uint32_t)(ev) >> MOY_INPUT_MODS_AT) & 0xFFu))
// The byte event `ev` types, 0 for none.
int32_t moy_input_typed(int32_t ev);

extern const char *const MOY_INPUT_NAMES[MOY_INPUT_BUTTONS];

typedef struct moy_input moy_input_t;

// The frame's pointer sample (moy_input_sample after begin_frame).
typedef struct {
    bool any;               // some source has written a sample
    bool down;
    bool edge;              // went down this frame
    bool fresh;             // measured this frame, not a held repeat
    int32_t x, y;
} moy_input_sample_t;

#define MOY_INPUT_TICKS_PERIOD (1u << 30)  // the VM's ticks_ms period

// a - b on that clock, wrapping.
static inline int32_t moy_input_ticks_diff(uint32_t a, uint32_t b) {
    const uint32_t half = MOY_INPUT_TICKS_PERIOD / 2u;
    return (int32_t)((a - b + half) & (MOY_INPUT_TICKS_PERIOD - 1u)) - (int32_t)half;
}

// A screen-space cursor: the trackball moves it, touch places it. Times are
// ms on the binding's clock, taken modulo MOY_INPUT_TICKS_PERIOD and compared
// wrapping.
typedef struct {
    int32_t w, h, x, y;
    int32_t idle_ms;
    uint32_t sampled;       // the last sample, moved or placed; meaningful once `touched`
    uint32_t last_move;     // the last trackball move: the cursor's idle clock
    bool click;             // went down this frame
    bool down;              // held this frame
    bool hovers;            // the source reports a position with nothing held
    bool fresh;             // this frame's sample came from the hardware
    bool visible;
    bool touched;           // a move or a place has happened: a pointer nothing touched is never live
    int32_t div;            // glass pixels per surface pixel: a placed position is divided by it
} moy_input_ptr_t;

moy_input_t *moy_input_new(uint8_t nbuttons);
void moy_input_free(moy_input_t *t);
// The drivers' table: all fifteen buttons, made on first call, never freed.
moy_input_t *moy_input_kernel(void);
uint8_t moy_input_nbuttons(const moy_input_t *t);

// The source named `name` (1 .. MOY_INPUT_NAME bytes), made on first ask with
// `player`; asking again returns the same handle and leaves its player alone.
int moy_input_source(moy_input_t *t, const char *name, uint8_t player, uint32_t *h);
int moy_input_source_name(const moy_input_t *t, uint32_t h, char *out, size_t n);
int moy_input_source_player(const moy_input_t *t, uint32_t h, uint8_t *player);
int moy_input_set_player(moy_input_t *t, uint32_t h, uint8_t player);

// -- what a producer writes: the source's latch, from a task or an ISR --
int moy_input_set_held(moy_input_t *t, uint32_t h, uint8_t button, bool held);
int moy_input_set_mask(moy_input_t *t, uint32_t h, uint32_t held);   // the whole held set
int moy_input_release(moy_input_t *t, uint32_t h);                   // this source holds nothing
int moy_input_set_key(moy_input_t *t, uint32_t h, int32_t key);      // the key event, as a level; drops the queue
int moy_input_key(moy_input_t *t, uint32_t h, int32_t key);          // one-shot: one frame
int moy_input_set_mods(moy_input_t *t, uint32_t h, uint8_t mods);    // the modifiers it holds now
int moy_input_set_typing(moy_input_t *t, uint32_t h, int32_t key);   // the typing level, 0 none
int moy_input_scroll(moy_input_t *t, uint32_t h, int32_t dx, int32_t dy);  // wheel notches, added
int moy_input_set_pointer_buttons(moy_input_t *t, uint32_t h, uint8_t held);
int moy_input_source_key(const moy_input_t *t, uint32_t h, int32_t *key);
uint8_t moy_input_source_mods(const moy_input_t *t, uint32_t h);
uint8_t moy_input_source_pointer_buttons(const moy_input_t *t, uint32_t h);
uint32_t moy_input_source_held(const moy_input_t *t, uint32_t h);
int32_t moy_input_source_typing(const moy_input_t *t, uint32_t h);
// This source's pointer sample, which stands until it writes another.
int moy_input_point(moy_input_t *t, uint32_t h, int32_t x, int32_t y, bool down, bool edge,
                    bool fresh);

// -- the event rings --
// The source's ring made again with `prov` (MOY_EV_ISR, _FIFO, _POLL) and
// `depth` records (a power of two, at most MOY_EV_DEPTH_MAX), empty; a
// producer names them before it first writes.
int moy_input_source_events(moy_input_t *t, uint32_t h, uint8_t prov, uint32_t depth);
// A record a producer writes itself (a kind no latch has), stamped now.
int moy_input_post(moy_input_t *t, uint32_t h, uint8_t kind, uint16_t a, uint16_t id, uint32_t b);
typedef struct {
    uint32_t depth, head, count, drops;
    uint8_t prov;
    bool internal;
    uint16_t src;                   // the records' `src`
} moy_input_ring_info_t;
int moy_input_ring_info(const moy_input_t *t, uint32_t h, moy_input_ring_info_t *out);
// The name of the source whose records carry `src` (moy_event_t.src), or
// MOY_INPUT_STALE when none does now.
int moy_input_src_name(const moy_input_t *t, uint16_t src, char *out, size_t n);

// A ring source (above): `name` (1 .. MOY_INPUT_NAME bytes), `prov` and `depth`
// as moy_input_source_events takes them. MOY_INPUT_FULL when every ring slot is
// taken, MOY_INPUT_NOMEM when its ring is not (RINGS refused it, or the heap).
int moy_input_ring_open(moy_input_t *t, const char *name, uint8_t prov, uint32_t depth,
                        uint32_t *h);
// The source ended: its ring freed and given back, every reader's cursor on it
// gone, its handle stale. An ISR writer is detached before this.
int moy_input_ring_close(moy_input_t *t, uint32_t h);
// An ISR's writer: internal RAM, the producer's. moy_input_isr_attach points it
// at an ISR ring source (MOY_INPUT_BAD for a source that is not one);
// moy_input_isr_detach makes every later put a no-op, under the ring's lock, so
// an ISR still running on the other core writes nothing once it returns.
typedef struct moy_input_ring moy_input_ring_t;
typedef struct {
    void *mux;                      // the table's lock (portMUX_TYPE on a board)
    moy_input_ring_t *volatile ring;
} moy_input_isr_t;
int moy_input_isr_attach(moy_input_t *t, uint32_t h, moy_input_isr_t *w);
void moy_input_isr_detach(moy_input_isr_t *w);
// One record, stamped now; callable from an ISR (MOY_INPUT_ISR_TEXT).
void moy_input_isr_put(moy_input_isr_t *w, uint8_t kind, uint16_t a, uint16_t id, uint32_t b);
// A reader of `kinds` (MOY_EV_BIT of each; MOY_EV_ALL) into *r; MOY_INPUT_FULL
// when MOY_EV_READERS are taken.
int moy_input_reader(moy_input_t *t, uint32_t kinds, int *r);
int moy_input_reader_free(moy_input_t *t, int r);
// Reader r reads source `h` alone (a lease's ring, routed to its process), or
// every source again for h 0. Once that source closes the reader reads nothing.
int moy_input_reader_only(moy_input_t *t, int r, uint32_t h);
// Up to `n` of the reader's records, oldest first across its sources, its
// cursors past them; a LOST record stands where a gap was. Allocates nothing.
size_t moy_input_events(moy_input_t *t, int r, moy_event_t *out, size_t n);
// How many records (a gap counting one) the reader has waiting.
size_t moy_input_pending(moy_input_t *t, int r);
// The records' clock: microseconds, wrapping at 2^32 (a test pins it; 0: the
// real one).
uint32_t moy_input_now_us(void);
void moy_input_pin_clock(uint32_t us);

// -- the frame --
// The latches merged, the edges and the repeats computed; `now` is the
// caller's clock in ms (wrapping on MOY_INPUT_TICKS_PERIOD), 0 for none.
void moy_input_begin_frame(moy_input_t *t, uint32_t now);
// The repeat's delay and period in ms (a period of 0 repeats nothing).
void moy_input_set_repeat(moy_input_t *t, int32_t delay_ms, int32_t rate_ms);
void moy_input_get_repeat(const moy_input_t *t, int32_t *delay_ms, int32_t *rate_ms);
// A producer's steps on `button` this frame, added to its repeat count.
int moy_input_add_steps(moy_input_t *t, uint8_t button, uint32_t n);
// The trackball's coast as this build has it (MOY_INPUT_BALL_COAST), and its
// rate (rate, NULL: not asked).
int32_t moy_input_ball_coast(int32_t *rate);
// Everybody lets go: every source and the merged set; the edge snapshot stays.
void moy_input_release_all(moy_input_t *t);
// A surface took this frame's edges and counts: nothing after it sees them,
// and a button still held stays held, so it neither presses again nor loses
// its place in the repeat.
void moy_input_spend_edges(moy_input_t *t);
// The same, and every held repeat's delay starts over: a modal opening on a
// key must not act on it.
void moy_input_clear_edges(moy_input_t *t);
// A paced cart's edges (#217): kept when no logic tick ran this frame, taken by
// the first tick of a frame, none for a second tick in the same frame.
void moy_input_keep_edges(moy_input_t *t);
void moy_input_tick_edges(moy_input_t *t);
void moy_input_drop_edges(moy_input_t *t);

// -- what a surface reads, after begin_frame --
// Held and pressed bits for `player`, MOY_INPUT_UNION for every source.
void moy_input_masks(const moy_input_t *t, uint8_t player, uint32_t *held, uint32_t *pressed);
uint32_t moy_input_released(const moy_input_t *t);
void moy_input_sample(const moy_input_t *t, moy_input_sample_t *out);
uint32_t moy_input_kept(const moy_input_t *t);   // the press edges a paced cart's next tick takes
// The source that brought news -- a button going down, a new key, a press of
// its pointer -- at the latest merge that had any, once: 1 and its handle at
// the first ask after it, 0 until a merge brings more. The console reads the
// input in use from it (the hints draw that input's keys).
int moy_input_news(moy_input_t *t, uint32_t *h);
// This frame's repeats (and added steps) of `button`, and of the four
// directions packed a byte each: left, right << 8, up << 16, down << 24.
uint32_t moy_input_repeats(const moy_input_t *t, uint8_t button);
uint32_t moy_input_rep_word(const moy_input_t *t);
// This frame's repeats of the typing level, and (ev, NULL: not asked) the key
// event they repeat.
uint32_t moy_input_key_repeats(const moy_input_t *t, int32_t *ev);
int32_t moy_input_last_key(const moy_input_t *t);    // the byte the frame's key event types
int32_t moy_input_key_event(const moy_input_t *t);   // the frame's key event
void moy_input_set_last_key(moy_input_t *t, int32_t key);   // a direct write of an event; the next source write heals it
uint8_t moy_input_mods(const moy_input_t *t);
// The frame's wheel notches, every source's summed.
void moy_input_wheel(const moy_input_t *t, int32_t *dx, int32_t *dy);
// The pointer's buttons held this frame, every source's; `pressed` (NULL:
// not asked) the ones that went down at this merge.
uint8_t moy_input_pointer_buttons(const moy_input_t *t, uint8_t *pressed);
bool moy_input_cart_keys(const moy_input_t *t);
void moy_input_set_cart_keys(moy_input_t *t, bool on);
// The distinct player slots the sources sit on, in the order a source first
// took each; returns how many (at least one).
uint8_t moy_input_players(const moy_input_t *t, uint8_t *out);
bool moy_input_multi(const moy_input_t *t);   // the sources sit on more than one slot
bool moy_input_text_mode(const moy_input_t *t);
void moy_input_set_text_mode(moy_input_t *t, bool on);

// -- the pointer --
void moy_input_ptr_init(moy_input_ptr_t *p, int32_t w, int32_t h, int32_t idle_ms, uint32_t now);
void moy_input_ptr_move(moy_input_ptr_t *p, int32_t dx, int32_t dy, uint32_t now);
void moy_input_ptr_place(moy_input_ptr_t *p, int32_t x, int32_t y, uint32_t now);
bool moy_input_ptr_live(const moy_input_ptr_t *p, uint32_t now);
void moy_input_ptr_tick(moy_input_ptr_t *p, uint32_t now);
// The frame's sample into the pointer: down and fresh, and the place while
// down. Returns MOY_INPUT_P_HELD while down, with MOY_INPUT_P_CLICK on its
// press edge; 0, and the pointer untouched, when no source has a sample.
int moy_input_ptr_apply(const moy_input_t *t, moy_input_ptr_t *p, uint32_t now);

#endif // MOY_INPUT_H

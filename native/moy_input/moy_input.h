// moy_input: the merged input table every surface reads, its sources and the
// pointer (docs/kernel_survival_2026-10.md section 4.1).
//
// One table body for every tier: the boards' kernel table (moy_input_kernel),
// the tables the MicroPython binding (modmoy_input.c) and the host's ctypes
// binding (runtime/moy_input.py) make, and the fuzzer's.
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
//
// THE POINTER. A source may also hold a pointer sample -- a place, down, the
// press edge and whether the hardware measured it this frame -- written like
// its buttons. The frame's sample is the merge: the newest sample of a source
// that is down, else the newest of any; an edge is delivered once. The touch
// driver and the dev channel's gestures are two such sources, so a scripted
// tap reaches the pointer the way a finger does.
//
// PLAYERS. A source sits on a player slot (0 .. MOY_INPUT_PLAYERS-1). The
// union (MOY_INPUT_UNION) is every source; a player's view is its sources'.
// While every source sits on one slot, that slot's view is the union and
// every other slot's is empty.
//
// A handle is the spine's: gen << 12 | MOY_KIND_SRC << 8 | slot, generation 1
// (a source is never released). Slot 0 is the table's own source, "local".

#ifndef MOY_INPUT_H
#define MOY_INPUT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

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
#define MOY_INPUT_PLAYERS 8u
#define MOY_INPUT_UNION 0xFFu
#define MOY_INPUT_KEYQ 8u           // one-shot keys a source queues
#define MOY_INPUT_NAME 15u          // a source name's bytes

#define MOY_INPUT_CURSOR_IDLE_MS 2000
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
    uint32_t sampled;       // the last sample, moved or placed
    uint32_t last_move;     // the last trackball move: the cursor's idle clock
    bool click;             // went down this frame
    bool down;              // held this frame
    bool hovers;            // the source reports a position with nothing held
    bool fresh;             // this frame's sample came from the hardware
    bool visible;
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
int moy_input_scroll(moy_input_t *t, uint32_t h, int32_t dx, int32_t dy);  // wheel notches, added
int moy_input_set_pointer_buttons(moy_input_t *t, uint32_t h, uint8_t held);
int moy_input_source_key(const moy_input_t *t, uint32_t h, int32_t *key);
uint8_t moy_input_source_mods(const moy_input_t *t, uint32_t h);
uint8_t moy_input_source_pointer_buttons(const moy_input_t *t, uint32_t h);
uint32_t moy_input_source_held(const moy_input_t *t, uint32_t h);
// This source's pointer sample, which stands until it writes another.
int moy_input_point(moy_input_t *t, uint32_t h, int32_t x, int32_t y, bool down, bool edge,
                    bool fresh);

// -- the frame --
void moy_input_begin_frame(moy_input_t *t);   // the latches merged, the edges computed
// Everybody lets go: every source and the merged set; the edge snapshot stays.
void moy_input_release_all(moy_input_t *t);
// The edges this frame are none: a modal opening on a key must not act on it.
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

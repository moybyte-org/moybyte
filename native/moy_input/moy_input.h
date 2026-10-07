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

#define MOY_INPUT_BUTTONS 15u
#define MOY_INPUT_HOST_BUTTONS 8u
#define MOY_INPUT_SOURCES 12u
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

extern const char *const MOY_INPUT_NAMES[MOY_INPUT_BUTTONS];

typedef struct moy_input moy_input_t;

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
int moy_input_set_key(moy_input_t *t, uint32_t h, int32_t key);      // the key, as a level; drops the queue
int moy_input_key(moy_input_t *t, uint32_t h, int32_t key);          // one-shot: one frame
int moy_input_source_key(const moy_input_t *t, uint32_t h, int32_t *key);
uint32_t moy_input_source_held(const moy_input_t *t, uint32_t h);

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
uint32_t moy_input_kept(const moy_input_t *t);   // the press edges a paced cart's next tick takes
int32_t moy_input_last_key(const moy_input_t *t);
void moy_input_set_last_key(moy_input_t *t, int32_t key);   // a direct write; the next source write heals it
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

#endif // MOY_INPUT_H

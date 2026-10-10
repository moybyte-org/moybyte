// moy_play: the kernel's Player (docs/kernel_cartpath_2026-10.md §3). What a
// cart's run is, decided and driven below the VM.
//
// THE VM-FREE RULE (§2), one function the census and the Player both call,
// decided from the catalogue entry, and for a compiled app from its module's
// imports too:
//   1. the runtime is "lua" or "wasm" and this image has its row (moy_rt_get);
//   2. the type is "game" (a manifest that names none is a game when it is a
//      spec cart, an app otherwise: runtime/moy_carts.py's rule), or "app"
//      with the runtime "wasm" where the image carries native/moy_app;
//   3. every permission is in the native set: graphics, input, audio,
//      multiplayer -- or, for a compiled app, a role's permission, which
//      passes when every row the module imports from the app extension is
//      C-served (moy_play_vm_free_at). A permission the kernel does not know
//      keeps the VM.
// A cart that fails it runs with the VM up, and `why` names the first clause
// it failed.
//
// THE CRASH RECORD (docs/kernel_cartpath_2026-10.md §8.3). `launch` arms the
// record's GAME role with a game's id (its folder's stem) before the runtime
// opens, and `end` clears it: a fault or a hang inside a run names its cart,
// and a game earns no strikes (no ledger counts the role). The board's kernel
// is what arms it (moy_kernel_arm); an image without one arms nothing.
//
// THE PLAYER (moy_play.c). One run at a time, named by a handle that is
// refused (STALE) once it ended. `launch`, before the runtime loads, reads
// the cart's catalogue entry for its runtime and verdict, takes that
// runtime's row from the map and mints the run's OWNER row in the glass
// (moy_buf.h), which a Lua run's layer and image pixels are loans to;
// `bind` gives the run its input table, its audio session and the tick model
// a paced run notes its costs into; `open` asks the row whether its runtime
// is open. `frame` runs the ticks the tick model planned: each takes the
// latched press edges (a paced run), fills the snapshot the cart reads from
// the input table and the pointer `input` published, runs the row's tick,
// notes its update half, and plays the audio it queued straight into the
// session; a quit() ends the frame where it stands. `end` closes the books:
// the run's upcalls by class since its launch, kept with its info for `state`,
// and ends its OWNER row, returning any loan the runtime's close left.
//
// The binding builds the run's console over Python's buffers before `open`,
// and closes it after `end`. A VM-free Lua run on a board whose compositor
// gives the kernel a front is the loop's foreground (below); every other run
// is driven by the console's frame upcall.

#ifndef MOY_PLAY_H
#define MOY_PLAY_H

#include <stdbool.h>
#include <stdint.h>

#include "moy_cat.h"

// The Player's answers, beside MOY_HTAB_*'s (moy_route.h's shape).
enum {
    MOY_PLAY_OK = 0, MOY_PLAY_STALE = 1, MOY_PLAY_FULL = 2, MOY_PLAY_NOMEM = 3,
    MOY_PLAY_NOCART = 4,    // no such cart, or it will not load
    MOY_PLAY_NORT = 5,      // its runtime is not in this image
    MOY_PLAY_NEWER = 6,     // a compiled cart imports what this console lacks
    MOY_PLAY_FIT = 7,       // a compiled cart's footprint is over what is free
    MOY_PLAY_RAISED = 8,    // the load, _init or a frame raised: the error panel
    MOY_PLAY_ENDED = 9,     // the run is over; its route is decided
    MOY_PLAY_NEEDS_VM = 10, // a Python-row call while no VM runs
};

// Why a run keeps the VM: the rule's clauses, in the order they are tested.
enum {
    MOY_PLAY_FREE = 0,          // no reason: the run needs no VM
    MOY_PLAY_WHY_BROKEN = 1,    // the manifest would not read
    MOY_PLAY_WHY_RUNTIME = 2,   // the runtime is Python, or one no row runs VM-free
    MOY_PLAY_WHY_NORT = 3,      // the runtime's row is not in this image
    MOY_PLAY_WHY_TYPE = 4,      // not a game
    MOY_PLAY_WHY_PERM = 5,      // a permission outside the native set
    MOY_PLAY_WHY_IMPORT = 6,    // a compiled app imports a row the Python console serves
    MOY_PLAY_WHY_COUNT = 7,
};

bool moy_play_vm_free(const moy_cat_entry_t *e, uint8_t *why);
// The verdict with the cart's folder: a compiled app ("runtime": "wasm",
// "type": "app") passes rule 2 where the image carries native/moy_app, and
// rule 3 maps each permission through the role table -- a role's permission
// passes when every import its module makes from the app extension
// (moybyte.app, native/moy_app/moy_app_wasm.h) is a C-served row, read from
// the head of its main; one that imports a shell-served row keeps the VM
// (WHY_IMPORT), the import named in `imp` ("moybyte.app.<name>").
bool moy_play_vm_free_at(const char *path, const moy_cat_entry_t *e, uint8_t *why,
                         char *imp, size_t cap);
// Rule 2 alone: the entry is a game, with the store's default.
bool moy_play_is_game(const moy_cat_entry_t *e);

// -- the Player ------------------------------------------------------------------

#include "moy_tick.h"

struct moy_input;                               // moy_input.h

// Launch flags: on the tick model; the run's route is HOME (the launcher
// started it, and its exit lands there); the owner's Unknown sources setting
// (an unsigned compiled module may load).
enum { MOY_PLAY_PACED = 1u, MOY_PLAY_HOME = 2u, MOY_PLAY_UNSIGNED = 4u };

// THE STOP VERDICT (docs/kernel_cartpath_2026-10.md section 5.1): whether the
// launch stops the VM for the run, and when it does not, the first clause
// that kept it. The policy is `need`: a VM-free run stops the VM only when
// its fit check fails with the VM up.
enum {
    MOY_PLAY_STOPS = 0,         // the VM stops; the load follows the stop
    MOY_PLAY_KEEP_RULE = 1,     // the cart fails the VM-free rule
    MOY_PLAY_KEEP_LEVER = 2,    // the board has no stop (the P4s: ABSENCE)
    MOY_PLAY_KEEP_FITS = 3,     // it fits with the VM up, or has no fit to check
    MOY_PLAY_KEEP_PLACE = 4,    // its route is not HOME: only the launcher comes back
    MOY_PLAY_KEEP_LEASE = 5,    // a Python owner holds a WiFi lease
    MOY_PLAY_KEEP_OTA = 6,      // an update is being written
    MOY_PLAY_KEEP_FRONT = 7,    // the board's kernel present cannot show its canvas
    MOY_PLAY_KEEP_BIG = 8,      // it would not fit with the VM stopped either: the
                                // fit notice, with the VM up
    MOY_PLAY_STOP_WHYS = 9,
};
const char *moy_play_stop_name(uint8_t why);
// The board's half of the verdict (native/moy_play/moy_play_stop.c, a board
// with the stop): every clause but the rule's. Weak: an image without it
// keeps the VM (KEEP_LEVER). `fit` gets the fit check's reading with the VM
// up when the verdict reached it (moy_play_info_t's `fit`).
uint8_t moy_play_stop_verdict(const moy_cat_entry_t *e, const char *path, uint32_t flags,
                              uint32_t fit[5]) __attribute__((weak));
// The live run's folder and launch flags (the stopped run's load reads them).
const char *moy_play_path(void);
uint32_t moy_play_flags(void);
// The live run runs with the VM down: its info says so, and the front takes
// a compiled cart, whose frame the kernel's present shows from the canvas.
void moy_play_set_down(uint32_t run);
enum { MOY_PLAY_QUIT = 1u, MOY_PLAY_VIEW = 2u };    // what a frame reports
enum {                                          // why a run ended
    MOY_PLAY_END_HOLD = 0, MOY_PLAY_END_QUIT = 1, MOY_PLAY_END_MENU = 2,
    MOY_PLAY_END_CRASH = 3, MOY_PLAY_END_LINK = 4, MOY_PLAY_END_SERIAL = 5,
};

// libmoy's seven buttons are the input table's first seven bits.
#define MOY_PLAY_BUTTON_MASK 0x7Fu
#define MOY_PLAY_HOME_BIT 7             // moy_input's `home`, after the seven
// A run handle's low byte: generation << 8 | this.
#define MOY_PLAY_TAG 0x5Au
#define MOY_PLAY_UPC 6          // MOY_UPC_CLASSES, the loop's crossing classes

typedef struct {
    int32_t x, y;               // the pointer in the cart's own coordinates
    int32_t touch;              // touch()'s flags: 1 live, 2 held, 4 the press edge
} moy_play_in_t;

typedef struct {
    char runtime[12];           // the manifest's, with the store's default
    char error[192];            // what the cart raised, when it did
    uint32_t frames, ticks;
    uint32_t upcalls[MOY_PLAY_UPC];    // since the launch, frozen at the end
    // The driving task's stack high-water mark (bytes never used) after the
    // open and after the last frame; MOY_PLAY_NO_STACK where there is no
    // meter (off a board).
    uint32_t stack_open, stack_frame;
    uint8_t why;                // MOY_PLAY_FREE, or the rule's clause
    uint8_t end_why;
    uint8_t stop_why;           // MOY_PLAY_STOPS, or the clause that kept the VM
    bool vm_down;               // the run ran with the VM stopped
    bool vm_free;
    bool raised;
    bool ended;
    bool game;                  // the manifest's type, with the store's default
    char title[48];             // the manifest's title, "" when it names none
    char id[24];                // the folder's stem: what the crash record names
    // The verdict's fit check, the VM up: the compiled cart's footprint (total,
    // block), PSRAM free and its largest block then, and the VM's heap bytes a
    // stop would give back. All 0: the verdict did not reach the fit.
    uint32_t fit[5];
    bool stuck;                 // the runaway watch ended its tick
} moy_play_info_t;

// The Player's row and the front's chrome list, made by `alloc` ahead of the
// first launch: a board's kernel makes them before the VM's first area, so
// neither a launch with the VM up nor a stopped run allocates anything that
// outlives it inside the free run a stop leaves. Otherwise the first launch
// and the first front make them.
void moy_play_reserve(void *(*alloc)(size_t n));
int  moy_play_launch(const char *cart, const char *caller, uint32_t flags, uint32_t *run);
int  moy_play_bind(uint32_t run, struct moy_input *in, uint32_t audio, moy_tick_t *tick);
int  moy_play_open(uint32_t run);
int  moy_play_input(uint32_t run, const moy_play_in_t *in);
// `ticks` logic ticks of `dt`, the last drawing when `render`; `out` gets
// MOY_PLAY_QUIT and MOY_PLAY_VIEW. OK, RAISED (the error in `info`), ENDED,
// NEEDS_VM or STALE.
int  moy_play_frame(uint32_t run, uint8_t ticks, float dt, bool render, uint32_t *out);
int  moy_play_end(uint32_t run, int why);
// The live run's info, or the last one's once it ended.
int  moy_play_info(uint32_t run, moy_play_info_t *out);
uint32_t moy_play_current(void);       // the live run, or 0
// The calling task's stack high-water mark in bytes, MOY_PLAY_NO_STACK off a
// board. FreeRTOS keeps the task's lifetime minimum, so a reading falls only
// when the cart's path goes deeper than anything the task ran before it.
#define MOY_PLAY_NO_STACK 0xFFFFFFFFu
uint32_t moy_play_stack_free(void);
uint32_t moy_play_last(void);          // the last run launched, live or ended
// THE RUNAWAY WATCH (docs/kernel_cartpath_2026-10.md §8.2, #212's rider).
// Each tick runs under a budget: MOY_PLAY_STUCK_SLOTS of the run's pacing
// slot (30 Hz unpaced), within [MOY_PLAY_STUCK_MIN_MS, MOY_PLAY_STUCK_MAX_MS],
// which is under every board's task-watchdog timeout. A watcher on another
// task -- an esp_timer on a board, a thread on the host; the browser's one
// thread has none -- polls every MOY_PLAY_WATCH_POLL_MS while a run is live,
// and ends a tick past its budget through moycore_stuck: a Lua run's count
// hook, installed from C outside the cart's reach, raises at its next
// instruction on the line it is on; a compiled cart is terminated and stops
// at its next import call. The run then reads as raised with `stuck` set,
// its error the panel's stuck words (moy_chrome_stuck_text) and the panel
// titled "stuck" (moy_chrome_crash_title), and the board says
// "PLAY stuck: <id>: <error>". A compiled loop that calls no import cannot be
// stopped (native/moy_wasm/README.md): the task watchdog resets the board,
// and the crash record names the cart through the GAME role armed above.
// A Python cart's tick is not watched (#212).
#define MOY_PLAY_STUCK_SLOTS 120u
#define MOY_PLAY_STUCK_MIN_MS 4000u
#define MOY_PLAY_STUCK_MAX_MS 10000u
#define MOY_PLAY_WATCH_POLL_MS 100u
// The watcher's poll: ends the live tick when it is past its budget; true
// when this poll fired.
bool moy_play_watch(uint32_t now_ms);
// The budget the next launch's ticks run under, in ms; 0 restores the slots'
// rule. The tests' lever.
void moy_play_watch_budget(uint32_t ms);
// A LOCKSTEP MATCH (moy_match.h) over the run, when the kernel's link has a
// live session: the frame's inputs are drained from the ring, and one advance
// runs when the session's tick is due or a stall retries (the newest packet
// is resent between ticks). `*ticks` is 1 when this frame simulates: the
// snapshot then reads the session's two global players, and libmoy's random
// stream starts from the frame's seed. False: no match, the run is solo.
bool moy_play_lockstep(uint32_t run, uint32_t now, uint8_t *ticks);
#define MOY_PLAY_LOCK_MS 16u            // a stall's retry and a resend: at most this often
// The seed a matched run's console starts from (its _init draws from it):
// true while the kernel's link has a live session.
bool moy_play_lock_seed(uint32_t *seed);
// THE KERNEL RUN AS THE LOOP'S FOREGROUND (docs/kernel_cartpath_2026-10.md
// §4). While a run is in front the loop's frame stage calls
// moy_play_front_frame and makes no console upcall: the run's input (the
// hold-to-exit gesture, the pointer), its ticks (the tick model's plan, or
// the match's lockstep), the chrome over it (the pill, the banner, the toast:
// moy_chrome's raster) and the board's present are all C. A board's
// compositor gives the front its two ends (moy_front_ops_t); an image whose
// board gives none keeps the console's foreground, which drives the same
// Player through its frame upcall.
//
// The front hands the frame back to the console -- the run still live --
// when the run needs it: the cart declared a view the board's present does
// not compose, it raised (the console's next frame reports it), or the link
// asked for a cart. It ENDS the run, freezing its books, when the run is
// over: the hold (END_HOLD), quit() (END_QUIT), a Ctrl-C (END_SERIAL), the
// dev channel's `end` (END_MENU). The console's next frame takes the route.
typedef struct {
    // The run's canvas this frame: its pixels and size (a banded board's
    // back buffer, which the cart draws straight into).
    uint16_t *(*canvas)(void *ctx, int *w, int *h);
    void (*present)(void *ctx, bool drew);
    // A panel point into the run's canvas; false when outside it.
    bool (*map)(void *ctx, int32_t *x, int32_t *y);
    void *ctx;
} moy_front_ops_t;
#define MOY_PLAY_HOLD_MS 700u           // the hold-to-exit gesture
#define MOY_PLAY_FREE_DT 0.1f           // a free-running cart's dt clamp
void moy_play_front_ops(const moy_front_ops_t *ops);
// Take the loop's foreground for the live run: OK, NORT where the board has
// no front or the run's canvas is not the one its present composes.
int moy_play_front(uint32_t run);
bool moy_play_front_live(void);
// One loop frame of the run in front, `dt_us` since the last: 1 drew, 0 did
// not, -1 the front gave the frame back (the run ended, or needs the console).
int moy_play_front_frame(uint32_t now, uint32_t dt_us);
// End the run in front (the dev channel's `end`, a Ctrl-C): false with none.
bool moy_play_front_end(int why);
// `state` while a run is in front: the kernel's JSON object (the shell's
// screen is the run's), or "null" with none in front. The bytes written.
size_t moy_play_state_json(char *out, size_t n);
// A Ctrl-C is waiting for the VM (the board's: weak, none elsewhere).
bool moy_play_interrupt_pending(void) __attribute__((weak));
// The map's rows this image has: lua and wasm where moycore and its engine
// are built, python where a VM registers it.
void moy_play_rows(bool lua, bool wasm, bool python);
// The reason's word, for `info`, `state` and the census: "free", "broken",
// "runtime", "absent", "type", "permission", "import".
const char *moy_play_why_name(uint8_t why);
// The census's line for an entry: "<runtime> <free|vm> <why>", the runtime
// with the store's default ("lua" for a spec cart that names none, "python"
// otherwise, "?" for one that is no string). snprintf's answer.
int moy_play_census_line(const moy_cat_entry_t *e, char *out, size_t n);
// The same with the cart's folder (moy_play_vm_free_at); a compiled app kept
// by an import reads "<runtime> vm import moybyte.app.<name>".
int moy_play_census_line_at(const char *path, const moy_cat_entry_t *e, char *out,
                            size_t n);

#endif // MOY_PLAY_H

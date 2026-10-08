// moy_loop: the console's frame, the kernel's (docs/kernel_survival_2026-10.md
// §7.1). One function, every tier: a board's VM service calls moy_loop_run()
// where the port would enter its REPL, the browser's worker and the host call
// moy_loop_step() once a frame, and Python is what the frame calls UP into.
//
// THE ORDER is the invariant the Python FrameLoop guarded, now one copy in C:
//
//   pump head -> inputs (EVERY source, the merge, the pointer's place) ->
//   dev channel -> idle ladder (after every input, so the waking touch is
//   swallowed) -> pointer (click latch + tick) -> present (pre-frame buffer
//   work: the P4's present_pending) -> the three console upcalls
//   (handle_input, handle_pointer, frame) -> the first-frame backlight gate ->
//   pump tail (first light's report, the OTA confirm) -> tail (the per-frame
//   services) -> pace -> account (PERF, the stage meters' window) -> the
//   watchdog feed -> the pacing sleep.
//
// The kernel's own draws (the HUD) land inside the frame upcall, through the
// canvas the console hands them, before its end_frame: the P4's last-write
// rule (#58).
//
// WHAT RUNS IT. A tier gives the loop its stages as an ops table (every entry
// optional: a stage with no op is never metered, so it reads as absent, not
// as free) and its upcalls through one dispatcher (moy_loop_set_upcall). The
// upcalls themselves are the binding's: on a VM they are root pointers the
// console's boot registered, cleared when the VM goes, and refused while none
// runs. The loop's state is one static struct, so the task or thread that
// calls step() may change between any two frames.
//
// UPCALLS ARE COUNTED BY CLASS, per frame and in total: CONSOLE is the draw
// stack's three and the console's dev words; APP an app's hooks; DRIVER a
// tier's harness; SERVICE a kernel service calling Python (the webhost's
// Python routes, the link's netplay drain) -- zero on a frame where nothing
// asked for it, which is the gate.

#ifndef MOY_LOOP_H
#define MOY_LOOP_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "moy_idle.h"
#include "moy_perf.h"

// moy_loop_step's answers.
enum {
    MOY_LOOP_OK = 0,
    MOY_LOOP_QUIT = 1,          // the dev channel asked for the REPL
    MOY_LOOP_INTERRUPT = 2,     // a Ctrl-C reached an upcall
    MOY_LOOP_STOPPED = 3,       // no VM, or no upcalls registered
    MOY_LOOP_EXIT = 4,          // a SystemExit reached an upcall: the VM's soft reset
};

// The upcalls the loop makes, and their classes.
enum {
    MOY_UP_INPUT = 0,           // ws.handle_input()
    MOY_UP_POINTER = 1,         // ws.handle_pointer()
    MOY_UP_FRAME = 2,           // ws.frame(dt) -> the console's frames drawn
    MOY_UP_WORD = 3,            // a dev-channel line the kernel's words left
    MOY_UP_SERVICE = 4,         // a kernel service's Python half
    MOY_UP_COUNT = 5,
};

enum {
    MOY_UPC_CONSOLE = 0,
    MOY_UPC_APP = 1,
    MOY_UPC_DRIVER = 2,
    MOY_UPC_SERVICE = 3,
    MOY_UPC_CLASSES = 4,
};

// What an upcall answers: >= 0 is its value (the frame's: the console's
// frames-drawn counter), these are its failures.
#define MOY_UP_RAISED (-1)       // an exception, reported and survived
#define MOY_UP_INTERRUPTED (-2)  // KeyboardInterrupt: the loop ends
#define MOY_UP_ABSENT (-3)       // nothing registered, or no VM
#define MOY_UP_EXIT (-4)         // SystemExit: the loop ends, the VM resets

// The stages, metered against shares of the pacing slot (#210). Same names
// and budgets as the Python meter this replaces.
enum {
    MOY_ST_INPUTS, MOY_ST_DEV, MOY_ST_IDLE, MOY_ST_POINTER, MOY_ST_PRESENT,
    MOY_ST_FRAME, MOY_ST_BACKLIGHT, MOY_ST_PUMP_TAIL, MOY_ST_TAIL, MOY_ST_PACE,
    MOY_ST_ACCOUNT, MOY_ST_COUNT,
};

typedef struct {
    uint32_t (*ticks_ms)(void);
    uint32_t (*ticks_us)(void);
    void (*sleep_ms)(uint32_t ms);
    // Every input source, the merge, the pointer's place: the frame's click
    // and whether anything was touched (the idle ladder's activity).
    void (*inputs)(uint32_t now, bool *click, bool *active);
    // The next byte the dev channel's line reader may take, or -1.
    int (*getc)(void);
    // The click into the pointer and its tick; `swallow`: the press that woke
    // the screen is dropped.
    void (*pointer)(uint32_t now, bool click, bool swallow);
    void (*present)(void);
    // The panel light, 0..255. Answers false where a level between off and
    // full is not possible (the dim rung is then absent).
    bool (*backlight)(int level);
    // No panel transfer in flight (the fence before first light).
    void (*fence)(void);
    // The idle ladder asks for a repaint: move the kernel epoch.
    void (*repaint)(void);
    // The first frame reached the glass, `ms` after the loop started.
    void (*first_light)(uint32_t ms);
    // The per-frame services at the tail; `drew` says a frame went out.
    void (*tail)(uint32_t now, bool drew);
    // The board's frame accounting beside the kernel's (its diag lines).
    void (*account)(uint32_t now, uint32_t elapsed, uint32_t sleep_ms);
    // The OTA confirm's two questions answered; called once.
    void (*healthy)(void);
    void (*feed)(void);
    // A line for serial (the loop's own reports, the kernel words' answers).
    void (*say)(const char *line);
    // The PERF line, where a board sends it somewhere besides serial too.
    void (*perf_line)(const char *line);
    // The compositor's overlap counters and the collector's pauses, for the
    // PERF line; false (or NULL) where the board has none.
    bool (*overlap)(moy_perf_overlap_t *out);
    bool (*gc_pauses)(uint32_t out[3]);
    // A dev-channel gesture's pointer sample into the channel's input source.
    void (*point)(int32_t x, int32_t y, bool down, bool edge);
    // The loop's own memory (PSRAM on a board); NULL: malloc.
    void *(*alloc)(size_t n);
    // The kernel services that want their Python half this frame (a bit
    // each, MOY_SVC_*), beside the ones the console asked for.
    uint32_t (*services)(void);
} moy_loop_ops_t;

// The services a frame's tail calls up into while they are live: the
// webhost's Python routes while it serves or says goodbye, the link's
// netplay drain while a match runs, the OTA confirm's marker once. One
// upcall a frame carries every bit; none while no bit is set.
enum {
    MOY_SVC_WEB = 1u << 0,
    MOY_SVC_LINK = 1u << 1,
    MOY_SVC_UPDATE = 1u << 2,
    MOY_SVC_HEALTHY = 1u << 3,
    MOY_SVC_DIAG = 1u << 4,
};

// The binding's upcall dispatcher: run upcall `which` (MOY_UP_*), with `arg`
// (the frame's dt in microseconds; the word's line), and answer its value or
// a MOY_UP_* failure.
typedef int (*moy_loop_up_fn)(int which, uint32_t arg, const char *line);

// -- the tier's side ----------------------------------------------------------

void moy_loop_init(const moy_loop_ops_t *ops, int fps_cap);
const moy_loop_ops_t *moy_loop_ops(void);
// A tier gave the loop its stages (moy_loop_init with an ops table).
bool moy_loop_has_ops(void);
void moy_loop_set_upcall(moy_loop_up_fn fn);
// Which of the three console upcalls are registered (a bit each); the loop
// runs only while all three are.
void moy_loop_set_registered(uint32_t bits);
uint32_t moy_loop_registered(void);
// A VM runs: registration and upcalls are allowed. A board clears it at the
// start of the VM's teardown and sets it once a fresh VM is up; the unix port
// and CPython never clear it.
void moy_loop_set_vm(bool up);
bool moy_loop_vm(void);

int moy_loop_step(void);
int moy_loop_run(void);

// A dev-channel line no kernel word took, to the console's words (counted
// CONSOLE); a kernel service's Python half (counted SERVICE).
int moy_loop_word(const char *line);
int moy_loop_service(uint32_t which);
void moy_loop_say(const char *fmt, ...);
void *moy_loop_alloc(size_t n);

// -- what the console tells the loop ------------------------------------------

// The loop's own cadence: frames a second while nothing is paced.
void moy_loop_set_fps(int fps_cap);
// The running cart's logic tick (ms), 0 while nothing is paced (#217).
void moy_loop_set_tick(uint32_t tick_ms);
// PERF DIAG: the stage meters and the PERF line follow it.
void moy_loop_set_capture(bool on);
bool moy_loop_capture(void);
// The OTA confirm is armed (an OTA build whose image is not yet valid).
void moy_loop_arm_health(bool on);
// The stage meters' window starts over, and the rest of this frame is not a
// sample of it (cart start and exit).
void moy_loop_meters_reset(void);
// The services the console holds live (the bits its service upcall answered
// with, or set here when one starts).
void moy_loop_set_services(uint32_t bits);
uint32_t moy_loop_services(void);
// The panel was lit before the loop (the boot splash did it).
void moy_loop_set_lit(bool lit);
// The console's half of the PERF line, which it fills when a sample is due
// (moy_loop_perf_due: PERF DIAG is on and this frame closes the period).
moy_perf_values_t *moy_loop_perf_console(void);
bool moy_loop_perf_due(void);

// -- the idle ladder -----------------------------------------------------------

moy_idle_t *moy_loop_idle(void);

// -- counts and meters ---------------------------------------------------------

// Upcalls by class: this frame's (the last finished frame's) and the total.
void moy_loop_count(int cls);
void moy_loop_upcalls(uint32_t frame[MOY_UPC_CLASSES], uint32_t total[MOY_UPC_CLASSES]);
uint32_t moy_loop_frames(void);         // loop iterations
uint32_t moy_loop_frame_at(void);       // the clock (ms) at the top of this frame
// The last finished frame's work (ms, the pacing sleep excluded) and sleep.
void moy_loop_last(uint32_t *elapsed, uint32_t *sleep);
uint32_t moy_loop_drawn(void);          // the console's frames drawn, last answer

typedef struct {
    int32_t budget_us;                  // -1: no deadline
    uint32_t avg_us, last_us, max_us, misses, n;
} moy_loop_meter_t;

const char *moy_loop_stage_name(int i);
// One stage's meter; false when the stage was never sampled.
bool moy_loop_meter(int i, moy_loop_meter_t *out);
uint32_t moy_loop_slot(void);

typedef struct {
    uint32_t frame_ms, slot, debt, slack, tick_ms;
} moy_loop_pump_t;

void moy_loop_pump(moy_loop_pump_t *out);
// The pump's pacing arithmetic on an injected elapsed: how long to sleep.
uint32_t moy_loop_pace(uint32_t elapsed);

#endif // MOY_LOOP_H

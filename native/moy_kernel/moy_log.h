// moy_log: the OS log (docs/os_2026-11/processes.md section 9), one ring of
// records `(t_us, level, pid, text)` every writer shares and every reader
// reads: `dmesg` and `logcat` in one.
//
// THE RECORD. moy_log_rec_t is the header, the text follows it in the ring:
// `t_us` the microseconds since boot, `seq` its number (1 the first this boot;
// a reader resumes from one), `pid` the process that wrote it, `level` a
// syslog severity (RFC 5424, MOY_LOG_EMERG .. MOY_LOG_DEBUG) and `len` the
// text's bytes, which may hold newlines (a traceback is one record). A text
// longer than MOY_LOG_TEXT_MAX keeps its first MOY_LOG_TEXT_MAX bytes.
//
// THE PROCESS ID. 0 is the kernel, 1 the shell; moy_log_spawn hands out the
// rest, one per run, in order, and names each. The pid of the code running now
// is `current` (moy_log_set_current): whoever starts a process's code sets it,
// and a writer that names no pid writes under it. A process table takes over
// spawn and current; the record does not change.
//
// THE RING is PSRAM (moy_kernel.h: kernel data is PSRAM), MOY_LOG_BYTES of it,
// made by moy_log_reserve before the VM's first heap area (moy_kernel.c's
// app_main) so a VM stop never finds it inside the run it frees; a tier with no
// reserve makes it at its first write. A write that does not fit drops the
// oldest records until it does. Writing allocates nothing and takes a lock
// around the copy, so any task may write; no ISR may.

#ifndef MOY_LOG_H
#define MOY_LOG_H

#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// The ring's bytes: configuration, a board's define over this default.
#ifndef MOY_LOG_BYTES
#define MOY_LOG_BYTES (16u * 1024u)
#endif
#define MOY_LOG_TEXT_MAX 2048u
#define MOY_LOG_NAMES 64u           // the newest runs whose names are kept
#define MOY_LOG_NAME_MAX 32u        // a name's bytes, its terminator included

enum {
    MOY_LOG_EMERG = 0, MOY_LOG_ALERT, MOY_LOG_CRIT, MOY_LOG_ERR, MOY_LOG_WARNING,
    MOY_LOG_NOTICE, MOY_LOG_INFO, MOY_LOG_DEBUG, MOY_LOG_LEVELS,
};

#define MOY_LOG_KERNEL 0u
#define MOY_LOG_SHELL 1u
// No pid named: the writer's is `current`.
#define MOY_LOG_CURRENT 0xFFFFFFFFu

typedef struct {
    uint64_t t_us;
    uint32_t seq;
    uint32_t pid;
    uint16_t len;
    uint8_t level;
    uint8_t flags;                  // 0; a bit is a later reader's
} moy_log_rec_t;

// The ring made (MOY_LOG_BYTES) before anything else allocates; idempotent.
void moy_log_reserve(void);
// The ring made again `bytes` large and emptied, pids and names kept: tests.
bool moy_log_init(size_t bytes);

// One record. `level` is clamped into the eight; `pid` MOY_LOG_CURRENT writes
// under `current`.
void moy_log_write(int level, uint32_t pid, const char *text, size_t n);
// One record of printf's format, at most 383 bytes.
void moy_log_say(int level, uint32_t pid, const char *fmt, ...);

// A new process's pid, named `name` (cut at MOY_LOG_NAME_MAX - 1 bytes).
uint32_t moy_log_spawn(const char *name);
// The pid's name: "kernel", "shell", a spawned one's, or "" once its name has
// been reused.
const char *moy_log_name(uint32_t pid);
uint32_t moy_log_current(void);
void moy_log_set_current(uint32_t pid);

// A reader's place: the next record it reads is the first whose seq is at
// least `seq`. Zeroed, it starts at the oldest record the ring holds.
typedef struct {
    uint32_t seq;
    uint32_t off;
} moy_log_cursor_t;

// The record at the cursor into `rec`, its text's first `cap` bytes into
// `text` (cap 0: none), the cursor past it. False at the ring's end.
bool moy_log_read(moy_log_cursor_t *c, moy_log_rec_t *rec, char *text, size_t cap);

typedef struct {
    uint32_t first;                 // the oldest record's seq (next when empty)
    uint32_t next;                  // the seq the next write takes
    uint32_t used, cap;             // bytes
} moy_log_stats_t;
void moy_log_stats(moy_log_stats_t *s);

// "emerg" .. "debug"; a level from its name, or -1.
const char *moy_log_level_name(int level);
int moy_log_level_of(const char *name, size_t n);

// One line a reader prints for a record's line: `LOG <seq> <s>.<ms> <level>
// <name>/<pid> <line>`, into `out`. Answers its length.
size_t moy_log_format(const moy_log_rec_t *r, const char *line, size_t n, char *out, size_t cap);

// Every record from `since` whose level is at most `level` and whose
// process's name holds `name` (NULL: every process), a `say` per line of its
// text, then `LOG end n=<records> next=<seq> first=<seq>`. The dev channel's
// `log` word.
void moy_log_dump(uint32_t since, int level, const char *name, void (*say)(const char *line));

// The clock a host's test pins (microseconds; 0: the real one).
void moy_log_pin_clock(uint64_t us);

// The exit statuses a process's `exit` line records (moy_proc_exit.h), in
// its order -- not run, interrupted, trapped, killed -- for a binding that
// reads them at run time (the host's ctypes, runtime/host/moy_log.py).
extern const uint8_t moy_log_exits[4];

#endif // MOY_LOG_H

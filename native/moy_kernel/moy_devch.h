// moy_devch: the serial dev channel's reader, the kernel's words and the
// scripted gestures (docs/kernel_survival_2026-10.md §7.3).
//
// THE READER takes the bytes the loop's getc op hands it, at most a budget a
// frame, and never blocks: a byte read is a byte consumed, so line noise costs
// a bounded slice of one frame. A line is everything up to CR or LF; an
// over-long partial line is dropped, and kilobytes with not one line in them
// disarm the channel once, out loud (a byte SOURCE, not a host: UART0's ISR on
// a floating pin reads exactly like that).
//
// A LINE goes to the kernel's word table first, and to the console's words (a
// MOY_UP_WORD upcall, counted CONSOLE) when no kernel word is its first token.
// `py` is the console's by definition; `recv` and `moy-put` take the stream
// from inside their upcall, so the reader hands a line over the moment its
// newline lands and reads nothing past it.
//
// THE GESTURES (`tap`, `swipe`, `drag`) play one pointer sample a frame into
// the channel's own input source, so the frame's merge hands them to the
// pointer exactly as it hands over a finger's. The console's words start them
// (a named button, the top window's title strip are the console's to find).

#ifndef MOY_DEVCH_H
#define MOY_DEVCH_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MOY_DEVCH_LINE_MAX 4096
#define MOY_DEVCH_BYTES_PER_FRAME 512
#define MOY_DEVCH_NOISE_LIMIT 16384
#define MOY_DEVCH_ARGS 8

// A kernel word: argv[0] is the word. Answers true when it handled the line;
// false hands it on to the console's words.
typedef bool (*moy_devch_word_fn)(int argc, char **argv, const char *line);

typedef struct {
    const char *name;
    moy_devch_word_fn fn;
} moy_devch_word_t;

// The reader starts armed, with an empty line. The gestures' samples go
// through the loop's `point` op.
void moy_devch_init(void);
// A tier's own words beside the kernel's (dispatched before them). One table.
void moy_devch_words(const moy_devch_word_t *words, int n);
void moy_devch_set_armed(bool on);
bool moy_devch_armed(void);
// The bytes a frame may take (moy-put raises it while a put streams).
void moy_devch_set_budget(uint32_t n);
// One frame: drain, run whole lines, advance a gesture. Answers whether a
// line ran or a gesture played (the idle ladder's activity); *quit when a
// word asked for the REPL.
bool moy_devch_poll(bool *quit);
// Run one line now, as if it had arrived (the host's harness, the tests).
bool moy_devch_line(const char *line, bool *quit);

// Bytes a word read past its end (moy-put's drain): the reader takes them
// before the next byte of the line, in order.
void moy_devch_unread(const uint8_t *bytes, size_t n);

void moy_devch_tap(int32_t x, int32_t y);
void moy_devch_swipe(int32_t x0, int32_t y0, int32_t x1, int32_t y1, int32_t frames);
void moy_devch_drag(int32_t cx, int32_t cy, int32_t frames, int32_t step);
bool moy_devch_gesture(void);

typedef struct {
    uint32_t rx, lines, dropped;
    bool armed;
} moy_devch_stats_t;

void moy_devch_stats(moy_devch_stats_t *out);

// The kernel's own words, for a binding that lists them.
int moy_devch_kernel_words(const moy_devch_word_t **out);

#endif // MOY_DEVCH_H

// moy_aud: the kernel's audio -- sessions over libmoy's synth, the one mix
// the speaker plays, and the sample voice (docs/kernel_survival_2026-10.md
// section 5). libmoy/ is the synth and is vendored; this is ours.
//
// A SESSION is a row of kind AUDIO in a table of MOY_AUD_SESSIONS, owned by an
// OWNER id (a cart run, the wallpaper, the Music editor): an owner holds at most
// one, and opening another for it closes the old one first. Each session has its
// own bank and its own libmoy state, both in PSRAM. FOCUS names the audible one;
// the others are muted, not stopped: their verbs change their state and nothing
// of it reaches the mix until they are focused. With no session focused the mix
// is silence (plus a compiled cart's stream).
//
// The LEVEL a session plays at is the lower of the console's (moy_aud_volume,
// the Settings row) and the session's own (moy_aud_level, SPEC.md 8.2's
// volume() verb), so a cart can turn itself down but never past the console.
//
// THE MIX (moy_aud_render) is the focused session's synth, then its sample
// voices, then the compiled cart's stream (moy_audio_snd.h), under the one lock.
// What pulls it is the board's feeder task (moy_aud_out.c) or, where there is
// none, the host's or the browser's per-frame pull.
//
// A bank is parsed OUTSIDE the lock into a fresh bank and swapped in under it,
// so the feeder never waits on a parse: the lock is held for a pointer swap and
// a voice reset, microseconds, whatever the bank's size.
//
// Every call returns MOY_AUD_OK or a code; a handle that names no live row is
// MOY_AUD_STALE and changes nothing.

#ifndef MOY_AUD_H
#define MOY_AUD_H

#include <stddef.h>
#include <stdint.h>

// ESP_PLATFORM is not defined for a usermod's sources on the esp32 port, so a
// board is recognised by the header it has.
#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#define MOY_AUD_BOARD 1
#endif
#endif
#ifndef MOY_AUD_BOARD
#define MOY_AUD_BOARD 0
#endif

#define MOY_AUD_SESSIONS 4u     // sessions at once: cart runs, the editor, the wallpaper
#define MOY_AUD_CLIPS 32u       // sample clips loaded at once
#define MOY_AUD_SAMPLE_VOICES 4 // per session, one per channel

enum {
    MOY_AUD_OK = 0,
    MOY_AUD_STALE = 1,      // the handle names no live session or clip
    MOY_AUD_FULL = 2,       // every row is taken
    MOY_AUD_NOMEM = 3,      // the allocator refused; nothing changed
    MOY_AUD_BANK = 4,       // the bank text was refused: the session plays a silent bank
    MOY_AUD_BAD = 5,        // an argument out of range
};

// -- sessions ---------------------------------------------------------------

// A session for `owner` (nonzero) over `bank_json` (`n` bytes, NULL for a
// silent bank). OK and BANK both leave `*s` live; it is not focused.
int moy_aud_open(uint32_t *s, uint32_t owner, const char *bank_json, size_t n);
// Replace the session's bank. Its voices and music stop.
int moy_aud_bank(uint32_t s, const char *bank_json, size_t n);
// Make `s` the audible session; 0 focuses none. The first focus of a live
// session starts the board's output (moy_aud_out_start), never earlier.
int moy_aud_focus(uint32_t s);
uint32_t moy_aud_focused(void);
// Voices silenced, bank and state freed; a focused session leaves the mix.
int moy_aud_close(uint32_t s);

// SPEC.md 8.2, one entry per verb, addressed to a session.
int moy_aud_sfx(uint32_t s, int n, int chan);          // chan < 0: libmoy's round-robin
int moy_aud_beep(uint32_t s, float freq_hz, float dur_s);
int moy_aud_music(uint32_t s, int track, int loop);
int moy_aud_music_stop(uint32_t s);
int moy_aud_stop(uint32_t s, int chan);                // chan < 0: everything
int moy_aud_level(uint32_t s, int level);              // the cart's volume(), 0..7

// Bit c per sounding voice, bit 4 a music track, bit 5 the beep, bit 7 a
// sample voice; bit 6 a compiled cart's stream holding frames. `s` 0: the
// focused session.
int moy_aud_active(uint32_t s, uint32_t *mask);

// The console's level, 0..7: every session plays at most this loud.
void moy_aud_volume(int level);
int moy_aud_console_level(void);
// Every session, every sample voice and the stream silent from the next
// chunk the mix renders. Nothing is closed.
void moy_aud_hush(void);

// -- the sample voice (#70) ---------------------------------------------------

// A clip of `frames` signed 16-bit mono frames at `rate` Hz, copied into PSRAM
// resampled to the mix's rate. The caller frees it; a playing voice stops then.
int moy_aud_sample_load(uint32_t *h, const int16_t *pcm, size_t frames, int rate);
// Play clip `h` on the session's sample channel `chan` (0..3; < 0 the first
// idle one, else channel 0), replacing what that channel played.
int moy_aud_sample_play(uint32_t s, uint32_t h, int chan);
int moy_aud_sample_free(uint32_t h);

// -- the mix ------------------------------------------------------------------

void moy_aud_set_rate(int rate);
int moy_aud_rate(void);
// Mix `n` frames into `out` (overwritten): what the speaker plays next.
void moy_aud_render(int16_t *out, int n);
// The self-dump: `n` frames of session `s`'s synth alone, rendered from its
// state into `out` without the speaker's mix. STALE, or BAD for the focused
// session, whose state the feeder is advancing.
int moy_aud_dump(uint32_t s, int16_t *out, int n);

// What the mix and the output have done since boot. The seam is rendered_out
// against written: a ratio above 1 is mixer time that never reached the
// speaker (a frame rendered by anything but the feeder, or a block it dropped).
typedef struct {
    uint32_t rendered;          // frames moy_aud_render mixed
    uint32_t written;           // frames the output accepted (the feeder task)
    uint32_t underruns;         // the output ring ran a whole ring behind
    uint32_t verbs;             // verb calls on any session
    uint32_t trig;              // sfx and music verbs counted for latency
    uint32_t trig_us_last;      // verb to the first chunk that mixed it, us
    uint32_t trig_us_max;
    uint32_t lock_wait_us_max;  // the longest the feeder waited for the lock
    uint32_t lock_hold_us_max;  // the longest a verb or a bank swap held it
    uint32_t bank_parse_us_max; // the longest parse, outside the lock
    uint32_t hush_at;           // `rendered` when the last hush came
    uint32_t loud_at;           // `rendered` at the end of the last chunk with a non-zero sample
    uint32_t rendered_out;      // `rendered` as the feeder's last write completed
} moy_aud_stats_t;

void moy_aud_stats(moy_aud_stats_t *st);
// Zero the latency and lock maxima (the counters keep counting).
void moy_aud_stats_reset_max(void);

// -- the trace ------------------------------------------------------------------

// While on, every session call is recorded: (slot, verb, a, b). The session
// trace in tests/test_semantic_traces.py and the drain's one-call-per-op count
// read it. Off by default; turning it on clears it.
enum {
    MOY_AUD_T_OPEN = 1, MOY_AUD_T_BANK, MOY_AUD_T_FOCUS, MOY_AUD_T_SFX,
    MOY_AUD_T_BEEP, MOY_AUD_T_MUSIC, MOY_AUD_T_MUSIC_STOP, MOY_AUD_T_STOP,
    MOY_AUD_T_LEVEL, MOY_AUD_T_CLOSE, MOY_AUD_T_VOLUME, MOY_AUD_T_HUSH,
    MOY_AUD_T_SAMPLE,
};
#define MOY_AUD_TRACE_ROWS 64u
void moy_aud_trace_on(int on);
// Copies up to `max` rows of four int32 each into `rows`; returns the rows.
size_t moy_aud_trace_read(int32_t *rows, size_t max);

// -- the output (moy_aud_out.c on a board; absent elsewhere) --------------------

enum {
    MOY_AUD_OUT_NONE = 0,       // never started (no session has been focused)
    MOY_AUD_OUT_RUNNING = 1,
    MOY_AUD_OUT_FAILED = 2,     // a start failed: this board has no audio
    MOY_AUD_OUT_ABSENT = 3,     // this build has no output (host, browser)
};
int moy_aud_out_start(void);    // idempotent; the state it leaves
// At boot, before any other driver opens the I2C bus: the codec's address on
// the kernel's bus (moy_bus.h), so the bus is the kernel's whoever opens it
// next. Nothing else: no register is written until the first start. OK on a
// board without a codec.
int moy_aud_out_attach(void);
int moy_aud_out_state(const char **why);
// The AUDIORATE line (the speaker's rate against the mix's, the seam, the
// ring's misses, the lock and the trigger latency, and a compiled cart's stream
// as queued/played/starved/room), written into `buf`, once
// per call with at least a second since the last; 0 when there is nothing new.
size_t moy_aud_out_probe(char *buf, size_t cap);

// Between the mix and the output: the lock (a no-op until a feeder exists),
// the clock, whether anything plays a compiled cart's stream, the shallow ring
// that stream asks for, and what the output counts into moy_aud_stats.
void moy_aud_lock(void);
int moy_aud_trylock(void);      // 1: taken without waiting
void moy_aud_unlock(void);
uint64_t moy_aud_now_us(void);
int moy_aud_out_plays(void);
void moy_aud_out_shallow(int on);
void moy_aud_count_out(uint32_t written, uint32_t underruns);
// A compiled cart's stream: queued, played, starved, room; 0 before any opened.
int moy_aud_snd_counts(uint32_t c[4], int *open);

#endif // MOY_AUD_H

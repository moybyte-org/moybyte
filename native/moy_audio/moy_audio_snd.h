// The render-ahead stream (docs/os_2026-11/hardware.md section 2) into this
// board's speaker: a compiled cart's `snd` (moy-spec SPEC.md 16.9), which
// moycore reaches from the engine's thread, its timed audio lane
// (native/moy_wasm/moy_wasm_session.h) or a Python process's
// `moybyte.snd`, one holder at a time. The stream is libmoy's moy_stream, and
// the feeder adds it after the synth in every block it renders, under the
// same master level.
//
// Built when moy_audio is in the image, whose cmake defines MOY_AUDIO_SND. A
// board without it has no speaker to reach, and moycore leaves the binding's
// `snd` callback NULL: the binding drains the stream by the clock instead.

#ifndef MOY_AUDIO_SND_H
#define MOY_AUDIO_SND_H

#include <stdint.h>

// moy_wasm.h's MOY_WASM_SND_RATE and MOY_WASM_SND_DEPTH; moycore checks that
// they agree.
#define MOY_AUDIO_SND_RATE  22050
#define MOY_AUDIO_SND_DEPTH 2048

// The ring's depth in frames an app may choose (hardware.md section 2's
// render-ahead ring): shallower is lower latency, deeper outlasts a longer
// stall. MOY_AUDIO_SND_DEPTH is moy-spec's and the default.
#define MOY_AUDIO_SND_MIN 64
#define MOY_AUDIO_SND_MAX 65536

// Open an empty stream `depth` frames deep (0: MOY_AUDIO_SND_DEPTH), its
// counters at zero. 1 when the feeder is running and will play it; 0 when
// nothing would, or the depth is outside MIN..MAX; -1 when a ring deeper than
// the default does not fit THE SHARES' RINGS row (native/moy_play/moy_play.h)
// or PSRAM. A deeper ring is the open's, taken from the row and given back at
// the close; the default's and a shallower one's is the reserved ring.
int moy_audio_snd_open(uint32_t depth);

// Make the stream's ring ahead of the first open (the board's kernel, before
// the VM's first area); the first open makes it otherwise.
void moy_aud_reserve(void);

// Queue up to `n` frames of little-endian signed 16-bit mono and return how
// many fitted; with `n` 0, the room. Any thread.
uint32_t moy_audio_snd(const uint8_t *pcm, uint32_t n);

// Drop what is queued and stop mixing. The counters stay readable.
void moy_audio_snd_close(void);

// The same from any task, the ring kept until the close: what a struck timed
// lane's monitor calls (native/moy_wasm/moy_wasm_session.h).
void moy_audio_snd_mute(void);

#endif

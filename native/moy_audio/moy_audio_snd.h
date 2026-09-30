// A compiled cart's sample stream (moy-spec SPEC.md 16.9, `snd`)
// into this board's speaker: the C surface moycore reaches from the engine's
// thread. The stream is libmoy's moy_stream, and the core-1 feeder adds it
// after the synth in every block it renders, under the same master level.
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

// Open an empty stream, its counters at zero. 1 when the feeder is running
// and will play it; 0 when nothing would (no stream is opened).
int moy_audio_snd_open(void);

// Queue up to `n` frames of little-endian signed 16-bit mono and return how
// many fitted; with `n` 0, the room. Any thread.
uint32_t moy_audio_snd(const uint8_t *pcm, uint32_t n);

// Drop what is queued and stop mixing. The counters stay readable.
void moy_audio_snd_close(void);

#endif

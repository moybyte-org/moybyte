/* moyhost_audio -- the HOST binding shim over vendored libmoy audio (#97, stage 0).
 *
 * The CPython sim loads this through ctypes (runtime/audio_binding.py builds it
 * at setup / first use), so the host's AudioEngine is the SAME C synthesizer the
 * boards and the web runner compile -- the hand-maintained Python twin it
 * replaced had a body count (the equal-loudness bug, the fractional-SFX-speed
 * bug: both were twin drift, invisible to every pixel gate).
 *
 * Deliberately the same shape as the device binding (modmoy_audio.c): libmoy
 * owns the bank, both sequencers and the mixer; this file only gives each
 * engine a place to live and forwards the six SPEC.md 8.2 verbs. Unlike the
 * device singleton, engines are malloc'd per handle -- the host opens one per
 * cart (Project._build_audio) and the wallpaper holds silent spares.
 *
 * Everything crosses as ints/doubles/text/buffers so the Python side needs no
 * struct layouts: a re-vendor that changes libmoy's structs recompiles this
 * shim against the new header and ctypes never notices. Doubles, not floats,
 * because the build compiles the DOUBLE-WIDENED copy of the vendored source
 * (the parity harness's own recipe, audio_parity._widen_to_double): the strict
 * parity suite proved the retired Python twin bit-identical to exactly that
 * program, so the swap changed no sample the host ever played.
 */

#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "moy_audio.h"

typedef struct {
    moy_bank  bank;
    moy_audio audio;
    int       rate;
} moyhost;

void *moyhost_new(int rate)
{
    moyhost *h = (moyhost *)calloc(1, sizeof(moyhost));
    if (h == NULL) {
        return NULL;
    }
    h->rate = rate;
    moy_bank_parse(&h->bank, NULL);             /* a valid, silent bank */
    moy_audio_init(&h->audio, &h->bank, rate);
    return h;
}

void moyhost_free(void *p)
{
    free(p);
}

/* Returns 1 on success, 0 on malformed JSON / over libmoy's capacities (the
 * bank is then zeroed and silent, exactly like the device). Rebinds + resets
 * the engine either way -- a new bank starts silent, as modmoy_audio does. */
int moyhost_bank_load(void *p, const char *json)
{
    moyhost *h = (moyhost *)p;
    int err = moy_bank_parse(&h->bank, json);
    moy_audio_init(&h->audio, &h->bank, h->rate);
    return err ? 0 : 1;
}

void moyhost_set_rate(void *p, int rate)
{
    moyhost *h = (moyhost *)p;
    h->rate = rate;
    moy_audio_init(&h->audio, &h->bank, rate);  /* re-times the engine; silent */
}

void moyhost_sfx(void *p, int n, int chan)
{
    moy_audio_sfx(&((moyhost *)p)->audio, n, chan);
}

void moyhost_beep(void *p, double freq_hz, double dur_s)
{
    moy_audio_beep(&((moyhost *)p)->audio, freq_hz, dur_s);
}

void moyhost_music(void *p, int track, int loop)
{
    moy_audio_music(&((moyhost *)p)->audio, track, loop);
}

void moyhost_music_stop(void *p)
{
    moy_audio_music_stop(&((moyhost *)p)->audio);
}

void moyhost_sound_stop(void *p, int chan)
{
    moy_audio_sound_stop(&((moyhost *)p)->audio, chan);
}

void moyhost_volume(void *p, int level)
{
    moy_audio_volume(&((moyhost *)p)->audio, level);
}

/* Bit mask, same layout as the device module's active(): bits 0..3 the four
 * voices, bit 4 the music track, bit 5 the beep. 0 == silence. */
unsigned moyhost_active(void *p)
{
    moyhost *h = (moyhost *)p;
    unsigned mask = 0;
    int i;
    for (i = 0; i < MOY_A_CHANNELS; i++) {
        if (h->audio.v[i].owner) {
            mask |= 1u << i;
        }
    }
    if (h->audio.track != NULL) {
        mask |= 1u << 4;
    }
    if (h->audio.bleft > 0.0) {
        mask |= 1u << 5;
    }
    return mask;
}

void moyhost_render(void *p, int16_t *out, int nframes)
{
    moy_audio_render(&((moyhost *)p)->audio, out, nframes);
}

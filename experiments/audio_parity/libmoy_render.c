/* libmoy_render -- render a scripted audio scenario through libmoy's SPEC.md 8
 * synth and dump raw signed-16-bit mono PCM.
 *
 * This is the REFERENCE half of the audio parity harness (the twin of
 * experiments/lua_bridge/host_parity.py for the Lua VM). The DEVICE and the web
 * runner compile libmoy itself, so they are conformant by construction; the host
 * sim cannot link C without putting a compiler in `make setup`, so
 * runtime/audio.py stays a Python twin of the same file. This program is what
 * keeps that twin honest: it renders a scenario through the real library, and
 * audio_parity.py renders the same scenario through the Python engine and
 * compares them.
 *
 * It builds against the VENDORED copy -- firmware/lilygo_t_deck_plus_micropython/
 * native/moy_audio/libmoy/ -- not a moy-spec checkout, so the harness measures
 * the source the boards actually compile and works in a fresh clone:
 *
 *   cc -std=c99 -O2 -I<native/moy_audio/libmoy> libmoy_render.c \
 *      <native/moy_audio/libmoy>/moy_audio.c -o libmoy_render
 *
 * (no -lm: libmoy's synth carries its own pitch table precisely so a firmware
 * needn't link libm.)
 *
 * Usage:  libmoy_render <script> <out.pcm>
 *
 * The script is one command per line. audio_parity.py owns the scenarios and
 * EMITS this file, then interprets the same command list itself, so both halves
 * are driven from one definition and cannot drift:
 *
 *   rate <hz>              sample rate (default 22050); before any render
 *   bank <path.json>       load a sounds.json bank
 *   sfx <n> [chan]         sfx(n, chan); chan omitted / -1 = round-robin
 *   beep <freq> <dur>      beep(freq, dur)
 *   music <track> [loop]   music(track, loop); loop defaults 1
 *   music_stop
 *   sound_stop [chan]      chan omitted = all
 *   volume <0..7>          master level
 *   render <nframes>       render nframes and append them to the output
 *   # ...                  comment
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_audio.h"

static moy_bank  bank;
static moy_audio audio;

/* Slurp a file into a NUL-terminated malloc'd buffer. */
static char *slurp(const char *path)
{
    FILE *f = fopen(path, "rb");
    long n;
    char *buf;
    if (!f) {
        fprintf(stderr, "libmoy_render: cannot open %s\n", path);
        exit(2);
    }
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    buf = malloc((size_t)n + 1);
    if (!buf || fread(buf, 1, (size_t)n, f) != (size_t)n) {
        fprintf(stderr, "libmoy_render: cannot read %s\n", path);
        exit(2);
    }
    buf[n] = '\0';
    fclose(f);
    return buf;
}

int main(int argc, char **argv)
{
    FILE *script, *out;
    char line[512];
    int rate = 22050, inited = 0;

    if (argc != 3) {
        fprintf(stderr, "usage: libmoy_render <script> <out.pcm>\n");
        return 2;
    }
    script = fopen(argv[1], "r");
    if (!script) {
        fprintf(stderr, "libmoy_render: cannot open %s\n", argv[1]);
        return 2;
    }
    out = fopen(argv[2], "wb");
    if (!out) {
        fprintf(stderr, "libmoy_render: cannot write %s\n", argv[2]);
        return 2;
    }
    memset(&bank, 0, sizeof bank);
    moy_audio_init(&audio, &bank, rate);

    while (fgets(line, sizeof line, script)) {
        char cmd[64];
        int a, b;
        double fa, fb;
        if (sscanf(line, "%63s", cmd) != 1 || cmd[0] == '#')
            continue;

        if (!strcmp(cmd, "rate")) {
            if (sscanf(line, "%*s %d", &rate) == 1 && !inited)
                moy_audio_init(&audio, &bank, rate);
        } else if (!strcmp(cmd, "bank")) {
            char path[400];
            char *text;
            if (sscanf(line, "%*s %399s", path) != 1) continue;
            text = slurp(path);
            if (moy_bank_parse(&bank, text)) {
                fprintf(stderr, "libmoy_render: bad bank %s\n", path);
                return 2;
            }
            free(text);
            moy_audio_init(&audio, &bank, rate);   /* rebind + reset */
            inited = 1;
        } else if (!strcmp(cmd, "sfx")) {
            b = -1;
            if (sscanf(line, "%*s %d %d", &a, &b) >= 1)
                moy_audio_sfx(&audio, a, b);
        } else if (!strcmp(cmd, "beep")) {
            if (sscanf(line, "%*s %lf %lf", &fa, &fb) == 2)
                moy_audio_beep(&audio, (float)fa, (float)fb);
        } else if (!strcmp(cmd, "music")) {
            b = 1;
            if (sscanf(line, "%*s %d %d", &a, &b) >= 1)
                moy_audio_music(&audio, a, b);
        } else if (!strcmp(cmd, "music_stop")) {
            moy_audio_music_stop(&audio);
        } else if (!strcmp(cmd, "sound_stop")) {
            a = -1;
            (void)sscanf(line, "%*s %d", &a);
            moy_audio_sound_stop(&audio, a);
        } else if (!strcmp(cmd, "volume")) {
            if (sscanf(line, "%*s %d", &a) == 1)
                moy_audio_volume(&audio, a);
        } else if (!strcmp(cmd, "render")) {
            int16_t *buf;
            if (sscanf(line, "%*s %d", &a) != 1 || a <= 0) continue;
            buf = calloc((size_t)a, sizeof *buf);
            if (!buf) return 2;
            moy_audio_render(&audio, buf, a);
            fwrite(buf, sizeof *buf, (size_t)a, out);
            free(buf);
        } else {
            fprintf(stderr, "libmoy_render: unknown command %s\n", cmd);
            return 2;
        }
    }
    fclose(script);
    fclose(out);
    return 0;
}

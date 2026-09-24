/*
 * doomgeneric's platform half for the #158 S3 spike: Doom as a WASM module
 * whose imports ARE the console. The host keeps time, feeds keys, reads the
 * WAD out of a flash partition, and blits the 8-bit frame through the
 * palette -- the "framebuffer verb" #158 says a real C cart needs, in its
 * simplest form: one linear-memory buffer, blitted once per frame.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "doomgeneric.h"
#include "doomkeys.h"
#include "w_file.h"
#include "z_zone.h"

#define IMPORT(name) __attribute__((import_module("env"), import_name(name)))
#define EXPORT(name) __attribute__((export_name(name)))

IMPORT("moy_ticks_ms") uint32_t moy_ticks_ms(void);
IMPORT("moy_sleep_ms") void moy_sleep_ms(uint32_t ms);
IMPORT("moy_get_key") uint32_t moy_get_key(void);   /* 0, or (pressed << 8) | key */
IMPORT("moy_draw") void moy_draw(const void *frame, const void *palette);
IMPORT("moy_wad_size") uint32_t moy_wad_size(void);
IMPORT("moy_wad_read") uint32_t moy_wad_read(uint32_t offset, void *dst, uint32_t len);

/* i_video.c's palette (CMAP256 build), gamma already applied. */
extern struct color { uint32_t b : 8, g : 8, r : 8, a : 8; } colors[256];

/* i_system.c shells out to show the crash text on a desktop; here it is
 * already on the serial console. */
int system(const char *command)
{
    (void)command;
    return -1;
}

void DG_Init(void) {}

void DG_DrawFrame(void)
{
    moy_draw(DG_ScreenBuffer, colors);
}

void DG_SleepMs(uint32_t ms)
{
    moy_sleep_ms(ms);
}

uint32_t DG_GetTicksMs(void)
{
    return moy_ticks_ms();
}

int DG_GetKey(int *pressed, unsigned char *key)
{
    uint32_t k = moy_get_key();
    if (!k) {
        return 0;
    }
    *pressed = (k >> 8) & 1;
    *key = k & 0xff;
    return 1;
}

void DG_SetWindowTitle(const char *title)
{
    (void)title;
}

/* ---- the WAD: one file, read through the host, replacing w_file.c ------- */

typedef struct {
    wad_file_t wad;
} moy_wad_file_t;

static wad_file_class_t moy_wad_file;

static wad_file_t *W_Moy_OpenFile(char *path)
{
    if (strcmp(path, "doom1.wad") != 0) {
        return NULL;
    }
    moy_wad_file_t *r = Z_Malloc(sizeof(*r), PU_STATIC, 0);
    r->wad.file_class = &moy_wad_file;
    r->wad.mapped = NULL;
    r->wad.length = moy_wad_size();
    return &r->wad;
}

static void W_Moy_CloseFile(wad_file_t *wad)
{
    Z_Free(wad);
}

static size_t W_Moy_Read(wad_file_t *wad, unsigned int offset, void *buffer,
                         size_t buffer_len)
{
    (void)wad;
    return moy_wad_read(offset, buffer, buffer_len);
}

static wad_file_class_t moy_wad_file = { W_Moy_OpenFile, W_Moy_CloseFile, W_Moy_Read };

wad_file_t *W_OpenFile(char *path)
{
    return moy_wad_file.OpenFile(path);
}

void W_CloseFile(wad_file_t *wad)
{
    wad->file_class->CloseFile(wad);
}

size_t W_Read(wad_file_t *wad, unsigned int offset, void *buffer, size_t buffer_len)
{
    return wad->file_class->Read(wad, offset, buffer, buffer_len);
}

/* ---- the entry points the host calls ------------------------------------ */

void doomgeneric_Tick(void);

EXPORT("dg_start") void dg_start(int zone_mb)
{
    static char mb[8];
    static char *argv[] = { "doom", "-iwad", "doom1.wad", "-mb", mb, "-nosound", NULL };
    snprintf(mb, sizeof(mb), "%d", zone_mb);
    setvbuf(stdout, NULL, _IONBF, 0);   /* every printf reaches the serial console at once */
    doomgeneric_Create(6, argv);
}

EXPORT("dg_tick") void dg_tick(void)
{
    doomgeneric_Tick();
}

/* Doom's tic counter, so a frame the host and the board both rendered at the
 * same gametic can be compared: the demo is deterministic per tic. */
extern int gametic;
EXPORT("dg_gametic") int dg_gametic(void)
{
    return gametic;
}

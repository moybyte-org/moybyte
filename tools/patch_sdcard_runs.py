#!/usr/bin/env python3
"""machine.SDCard moves sectors in multi-block runs, not one at a time.

    python3 tools/patch_sdcard_runs.py <micropython checkout>

`machine.SDCard.readblocks`/`writeblocks` hand the caller's buffer straight to
ESP-IDF's `sdmmc_read_sectors`/`sdmmc_write_sectors`. On an S3 that buffer is
in PSRAM -- the gc heap, a FAT sector cache -- which the SD host cannot DMA
from, and IDF's answer is to copy through a one-sector bounce and issue one
single-block command per sector. On a write each of those waits out the card's
busy time, which is most of what a sector costs, so a card that streams
multi-block writes writes sector by sector instead.

This copies through a bounce of up to MOYBYTE_SD_RUN sectors in internal DMA
memory and moves each run as one multi-block command. The bounce is taken per
call and given back, and halves until the allocator can give it; when not even
two sectors' worth is free the call goes to IDF as before. A buffer the host
can DMA from already goes to IDF whole, so that path is untouched.

The T-Deck's card is not `machine.SDCard` (native/moy_sd, which does the same
in its own module), and the P4s have no card in play: the Guition S3 is the
board that takes this. Applied by `moybyte_patch_sdcard_runs` in
tools/esp32_build_lib.sh, to `ports/esp32/machine_sdcard.c`. All-or-nothing:
every hunk must match exactly once or nothing is written and the exit is
non-zero, naming the hunk. A tree already carrying the marker is left as it is.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: SD sectors in multi-block runs"
RUN_SECTORS = 32

HELPER = """// %s (tools/patch_sdcard_runs.py).
#include "esp_heap_caps.h"
#include "esp_memory_utils.h"
#define MOYBYTE_SD_RUN %d
static esp_err_t moybyte_sd_xfer(sdmmc_card_t *card, uint8_t *buf, size_t start,
    size_t count, bool write) {
    size_t ss = card->csd.sector_size;
    if (esp_ptr_dma_capable(buf) && ((uintptr_t)buf & 3) == 0) {
        return write ? sdmmc_write_sectors(card, buf, start, count)
                     : sdmmc_read_sectors(card, buf, start, count);
    }
    size_t run = count < MOYBYTE_SD_RUN ? count : MOYBYTE_SD_RUN;
    uint8_t *tmp = NULL;
    while (run > 1) {
        tmp = heap_caps_malloc(run * ss, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
        if (tmp != NULL) {
            break;
        }
        run /= 2;
    }
    if (tmp == NULL) {
        return write ? sdmmc_write_sectors(card, buf, start, count)
                     : sdmmc_read_sectors(card, buf, start, count);
    }
    esp_err_t err = ESP_OK;
    for (size_t i = 0; i < count && err == ESP_OK; i += run) {
        size_t n = count - i < run ? count - i : run;
        if (write) {
            memcpy(tmp, buf + i * ss, n * ss);
            err = sdmmc_write_sectors(card, tmp, start + i, n);
        } else {
            err = sdmmc_read_sectors(card, tmp, start + i, n);
            if (err == ESP_OK) {
                memcpy(buf + i * ss, tmp, n * ss);
            }
        }
    }
    heap_caps_free(tmp);
    return err;
}

""" % (MARKER, RUN_SECTORS)

READ_FN = ("static mp_obj_t machine_sdcard_readblocks(mp_obj_t self_in, "
           "mp_obj_t block_num, mp_obj_t buf) {\n")

# (file, name, stock text, patched text). Each stock text must occur exactly
# once, or the whole patch is refused.
HUNKS = [
    ("ports/esp32/machine_sdcard.c", "the run helper", READ_FN, HELPER + READ_FN),
    ("ports/esp32/machine_sdcard.c", "readblocks",
     "    err = sdmmc_read_sectors(&(self->card), bufinfo.buf, "
     "mp_obj_get_int(block_num), bufinfo.len / _SECTOR_SIZE(self));\n",
     "    err = moybyte_sd_xfer(&(self->card), bufinfo.buf, "
     "mp_obj_get_int(block_num), bufinfo.len / _SECTOR_SIZE(self), false);\n"),
    ("ports/esp32/machine_sdcard.c", "writeblocks",
     "    err = sdmmc_write_sectors(&(self->card), bufinfo.buf, "
     "mp_obj_get_int(block_num), bufinfo.len / _SECTOR_SIZE(self));\n",
     "    err = moybyte_sd_xfer(&(self->card), bufinfo.buf, "
     "mp_obj_get_int(block_num), bufinfo.len / _SECTOR_SIZE(self), true);\n"),
]


def apply(mpy_dir):
    """Patch the checkout under `mpy_dir`; returns "patched", "already", or
    raises SystemExit naming the hunk that did not match exactly once."""
    paths = sorted({f for f, _, _, _ in HUNKS})
    texts = {}
    for rel in paths:
        with open(os.path.join(mpy_dir, rel), encoding="utf-8") as fh:
            texts[rel] = fh.read()
    if all(MARKER in texts[rel] for rel in paths):
        return "already"
    for rel, name, old, new in HUNKS:
        n = texts[rel].count(old)
        if n != 1:
            sys.exit("!! SD run patch did not apply -- %s hunk %r matches %d times "
                     "(the stock line changed shape); nothing written" % (rel, name, n))
        texts[rel] = texts[rel].replace(old, new)
    for rel in paths:
        with open(os.path.join(mpy_dir, rel), "w", encoding="utf-8") as fh:
            fh.write(texts[rel])
    return "patched"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mpy_dir", help="the MicroPython checkout to patch (its ports/esp32 is what changes)")
    a = ap.parse_args(argv)
    if apply(a.mpy_dir) == "patched":
        print("== patched ports/esp32/machine_sdcard.c: SD sectors in %d-sector runs"
              % RUN_SECTORS)


if __name__ == "__main__":
    main()

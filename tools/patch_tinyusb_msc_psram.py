#!/usr/bin/env python3
"""TinyUSB's MSC endpoint buffer lives in PSRAM, taken when the device starts,
not in internal .bss for the whole power-on.

    python3 tools/patch_tinyusb_msc_psram.py <micropython checkout>

The espressif/tinyusb component's MSC class keeps its endpoint buffer as a
static, `_mscd_epbuf`, of CFG_TUD_MSC_EP_BUFSIZE bytes: every image that
builds the class with native/moy_usbdisk carries it in internal DRAM whether
or not the drive ever runs. On the Waveshare P4, whose buffer is 8192 bytes (a
transfer's 16 sectors per callback, native/moy_usbdisk/moy_tusb_config.h),
that was 8256 bytes of the idle desk's internal heap. The patch makes it a
block `mscd_init` takes from PSRAM at the drive's first start (internal RAM
only where there is none) and keeps: this TinyUSB's MSC class has no deinit
hook to give it back from, and PSRAM is not the scarce region. The buffer is
safe there: the module builds TinyUSB with the DWC2's DMA off
(CFG_TUD_DWC2_DMA_ENABLE 0), so the CPU moves it to and from the FIFO, the
controller's interrupt is not an IRAM one, and the card side copies through a
bounce of its own (native/moy_sd's moy_sd_card_io). A start whose block
cannot be taken opens no MSC interface.

Applied by `moybyte_patch_tinyusb_msc_psram` in tools/esp32_build_lib.sh to
the component manager's copy, `ports/esp32/managed_components/
espressif__tinyusb/src/class/msc/msc_device.c`, once the build's submodules
step has fetched it. All-or-nothing: every hunk must match exactly once or
nothing is written and the exit is non-zero. A file already carrying the
marker is left as it is.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: the MSC endpoint buffer in PSRAM"
FILE = "ports/esp32/managed_components/espressif__tinyusb/src/class/msc/msc_device.c"

HUNKS = (
    # the buffer: a block and its pointer, the name its uses keep
    ('CFG_TUD_MEM_SECTION static struct {\n'
     '  TUD_EPBUF_DEF(buf, CFG_TUD_MSC_EP_BUFSIZE);\n'
     '} _mscd_epbuf;\n',
     '// %s, taken by mscd_init at the first\n'
     '// start and kept (tools/patch_tinyusb_msc_psram.py).\n'
     '#include "esp_heap_caps.h"\n'
     'typedef struct {\n'
     '  TUD_EPBUF_DEF(buf, CFG_TUD_MSC_EP_BUFSIZE);\n'
     '} moy_mscd_epbuf_t;\n'
     'static moy_mscd_epbuf_t *moy_mscd_epbuf;\n'
     '#define _mscd_epbuf (*moy_mscd_epbuf)\n' % MARKER),
    # mscd_init: the block, once
    ('void mscd_init(void) {\n'
     '  tu_memclr(&_mscd_itf, sizeof(mscd_interface_t));\n'
     '}\n',
     'void mscd_init(void) {\n'
     '  tu_memclr(&_mscd_itf, sizeof(mscd_interface_t));\n'
     '  if (moy_mscd_epbuf == NULL) {\n'
     '    moy_mscd_epbuf = heap_caps_malloc(sizeof(moy_mscd_epbuf_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);\n'
     '  }\n'
     '  if (moy_mscd_epbuf == NULL) {\n'
     '    moy_mscd_epbuf = heap_caps_malloc(sizeof(moy_mscd_epbuf_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);\n'
     '  }\n'
     '}\n'),
    # mscd_open: no interface without it
    ('uint16_t mscd_open(uint8_t rhport, tusb_desc_interface_t const * itf_desc, uint16_t max_len) {\n',
     'uint16_t mscd_open(uint8_t rhport, tusb_desc_interface_t const * itf_desc, uint16_t max_len) {\n'
     '  TU_VERIFY(moy_mscd_epbuf != NULL, 0);\n'),
)


def patch_text(text):
    """The patched source, or SystemExit naming the hunk that does not match
    exactly once."""
    for i, (stock, patched) in enumerate(HUNKS):
        n = text.count(stock)
        if n != 1:
            sys.exit("!! TinyUSB MSC PSRAM buffer patch did not apply -- hunk %d "
                     "matches %d times in %s (the component changed shape); "
                     "nothing written" % (i, n, FILE))
        text = text.replace(stock, patched)
    return text


def apply(mpy_dir):
    """Patch the checkout under `mpy_dir`; returns "patched", "already", or
    raises SystemExit when a hunk does not match exactly once."""
    path = os.path.join(mpy_dir, FILE)
    if not os.path.isfile(path):
        sys.exit("!! TinyUSB MSC PSRAM buffer patch: %s is not there (the "
                 "component manager has not fetched espressif/tinyusb)" % path)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if MARKER in text:
        return "already"
    text = patch_text(text)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return "patched"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mpy_dir", help="the MicroPython checkout whose managed "
                                    "tinyusb component is patched")
    a = ap.parse_args(argv)
    if apply(a.mpy_dir) == "patched":
        print("== patched %s: the MSC endpoint buffer is PSRAM" % FILE)


if __name__ == "__main__":
    main()

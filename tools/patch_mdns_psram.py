#!/usr/bin/env python3
"""The mDNS responder's scratch buffers live in PSRAM while it runs, not in
internal .bss for the whole power-on.

    python3 tools/patch_mdns_psram.py <micropython checkout>

The espressif/mdns component that MicroPython's esp32 port depends on keeps
four function-static buffers in `mdns.c`: the outgoing packet
(MDNS_MAX_PACKET_SIZE, 1460 B), the parser's name (an mdns_name_t, 263 B) and
two MDNS_NAME_BUF_LEN (65 B) name buffers. Every image that links the
component carries them in internal DRAM, whether or not the responder ever
starts; a console starts it only while its SSH server serves
(native/moy_net/moy_wifi.c's moy_wifi_mdns). The patch gathers them into one
block that `mdns_init` allocates in PSRAM (internal RAM only where there is
none) and `mdns_free` frees after the service task has stopped. All four are
used only on the service task, so one block serves them as the statics did.

ESP-IDF's other way, CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY, was measured
on both S3s and declined (2026-11-06, #246): its own linker fragments also
move the WiFi driver's, lwIP's, wpa_supplicant's and the BT host's .bss, and
the WiFi driver's start then takes more internal RAM than the move frees, so
a console that has had its radio up is left with less.

Applied by `moybyte_patch_mdns_psram` in tools/esp32_build_lib.sh to the
component manager's copy, `ports/esp32/managed_components/espressif__mdns/
mdns.c`, once the build's submodules step has fetched it. All-or-nothing:
every hunk must match exactly once or nothing is written and the exit is
non-zero. A file already carrying the marker is left as it is.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: the responder's scratch in PSRAM"
FILE = "ports/esp32/managed_components/espressif__mdns/mdns.c"

HUNKS = (
    # the block and its pointer, after the private header that defines the sizes
    ('#include "mdns_private.h"\n',
     '#include "mdns_private.h"\n'
     '#include "esp_heap_caps.h"\n'
     '\n'
     '// %s while it runs: the buffers that were\n'
     '// function-static, allocated by mdns_init and freed by mdns_free\n'
     '// (tools/patch_mdns_psram.py). Only the service task uses them.\n'
     'typedef struct {\n'
     '    uint8_t packet[MDNS_MAX_PACKET_SIZE];\n'
     '    mdns_name_t name;\n'
     '    char fqdn[MDNS_NAME_BUF_LEN];\n'
     '    char parse[MDNS_NAME_BUF_LEN];\n'
     '} moy_mdns_scratch_t;\n'
     'static moy_mdns_scratch_t *s_moy_scratch;\n' % MARKER),
    # _mdns_append_fqdn
    ('    mdns_name_t name;\n'
     '    static char buf[MDNS_NAME_BUF_LEN];\n'
     '    uint8_t len = strlen(strings[0]);\n',
     '    mdns_name_t name;\n'
     '    char *buf = s_moy_scratch->fqdn;\n'
     '    uint8_t len = strlen(strings[0]);\n'),
    # _mdns_dispatch_tx_packet
    ('    static uint8_t packet[MDNS_MAX_PACKET_SIZE];\n'
     '    uint16_t index = MDNS_HEAD_LEN;\n',
     '    uint8_t *packet = s_moy_scratch->packet;\n'
     '    uint16_t index = MDNS_HEAD_LEN;\n'),
    # _mdns_parse_fqdn
    ('    static char buf[MDNS_NAME_BUF_LEN];\n'
     '\n'
     '    const uint8_t *next_data = (uint8_t *)_mdns_read_fqdn(packet, start, name, buf, packet_len);\n',
     '    char *buf = s_moy_scratch->parse;\n'
     '\n'
     '    const uint8_t *next_data = (uint8_t *)_mdns_read_fqdn(packet, start, name, buf, packet_len);\n'),
    # mdns_parse_packet
    ('    static mdns_name_t n;\n'
     '    mdns_header_t header;\n'
     '    const uint8_t *data = _mdns_get_packet_data(packet);\n',
     '    mdns_header_t header;\n'
     '    const uint8_t *data = _mdns_get_packet_data(packet);\n'),
    ('    mdns_search_once_t *search_result = NULL;\n'
     '\n'
     '#ifdef MDNS_ENABLE_DEBUG\n',
     '    mdns_search_once_t *search_result = NULL;\n'
     '    mdns_name_t *const moy_name = &s_moy_scratch->name;\n'
     '\n'
     '#ifdef MDNS_ENABLE_DEBUG\n'),
    # mdns_init: the block with the server
    ('    memset((uint8_t *)_mdns_server, 0, sizeof(mdns_server_t));\n'
     '    // zero-out local copy of netifs',
     '    memset((uint8_t *)_mdns_server, 0, sizeof(mdns_server_t));\n'
     '    s_moy_scratch = heap_caps_calloc(1, sizeof(*s_moy_scratch), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);\n'
     '    if (!s_moy_scratch) {\n'
     '        s_moy_scratch = calloc(1, sizeof(*s_moy_scratch));\n'
     '    }\n'
     '    if (!s_moy_scratch) {\n'
     '        free(_mdns_server);\n'
     '        _mdns_server = NULL;\n'
     '        return ESP_ERR_NO_MEM;\n'
     '    }\n'
     '    // zero-out local copy of netifs'),
    # mdns_init's failure path
    ('free_server:\n'
     '    free(_mdns_server);\n'
     '    _mdns_server = NULL;\n'
     '    return err;\n',
     'free_server:\n'
     '    free(_mdns_server);\n'
     '    _mdns_server = NULL;\n'
     '    free(s_moy_scratch);\n'
     '    s_moy_scratch = NULL;\n'
     '    return err;\n'),
    # mdns_free, after the service task has stopped
    ('    vSemaphoreDelete(_mdns_server->action_sema);\n'
     '    free(_mdns_server);\n'
     '    _mdns_server = NULL;\n'
     '}\n',
     '    vSemaphoreDelete(_mdns_server->action_sema);\n'
     '    free(_mdns_server);\n'
     '    _mdns_server = NULL;\n'
     '    free(s_moy_scratch);\n'
     '    s_moy_scratch = NULL;\n'
     '}\n'),
)

# mdns_parse_packet's one use of its static: `mdns_name_t *name = &n;`, the
# first such line after the function's opening (the debug dump below it has
# its own, compiled out).
PARSE_HEAD = "void mdns_parse_packet(mdns_rx_packet_t *packet)\n{\n"
PARSE_USE = "    mdns_name_t *name = &n;\n"


def patch_text(text):
    """The patched source, or SystemExit naming the hunk that does not match
    exactly once."""
    for i, (stock, patched) in enumerate(HUNKS):
        n = text.count(stock)
        if n != 1:
            sys.exit("!! mdns PSRAM scratch patch did not apply -- hunk %d matches "
                     "%d times in %s (the component changed shape); nothing written"
                     % (i, n, FILE))
        text = text.replace(stock, patched)
    head = text.find(PARSE_HEAD)
    use = text.find(PARSE_USE, head)
    if head < 0 or use < 0 or text.find("\n}\n", head) < use:
        sys.exit("!! mdns PSRAM scratch patch did not apply -- mdns_parse_packet's "
                 "`%s` is not in its body; nothing written" % PARSE_USE.strip())
    return (text[:use] + "    mdns_name_t *name = moy_name;\n"
            + text[use + len(PARSE_USE):])


def apply(mpy_dir):
    """Patch the checkout under `mpy_dir`; returns "patched", "already", or
    raises SystemExit when a hunk does not match exactly once."""
    path = os.path.join(mpy_dir, FILE)
    if not os.path.isfile(path):
        sys.exit("!! mdns PSRAM scratch patch: %s is not there (the component "
                 "manager has not fetched espressif/mdns)" % path)
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
                                    "mdns component is patched")
    a = ap.parse_args(argv)
    if apply(a.mpy_dir) == "patched":
        print("== patched %s: the responder's scratch is PSRAM while it runs" % FILE)


if __name__ == "__main__":
    main()

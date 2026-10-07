"""The links' drivers call no Python (docs/kernel_survival_2026-10.md section
6.9): what runs on the WiFi task, or below the VM between frames, is C that
names nothing of the VM's, so a VM stop or a soft reset cannot leave it calling
into a heap that is gone. And the console images leave the port's espnow
module out, so nothing in Python can take esp_now's one receive callback from
the kernel's link."""

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET = os.path.join(ROOT, "native", "moy_net")

# Every moy_net source but the module face (modmoy_net.c) and the fuzz driver.
DRIVERS = ("moy_http.c", "moy_sync.c", "moy_wifi.c", "moy_link.c", "moy_link.h",
           "moy_net.h")

CONSOLES = {
    "lilygo_t_deck_plus_mainline": "MOYBYTE_TDECK",
    "guition_jc3248w535": "MOYBYTE_GUITION_S3",
    "esp32_p4_wifi6_touch_lcd_7b": "MOYBYTE_P4",
    "guition_jc8012p4a1c": "MOYBYTE_GUITION_P4",
}

# The port's configuration header is the one VM header a driver reads: it
# carries the board's defines.
_VM = re.compile(r'\bmp_[a-z_]+\s*\(|\bMP_[A-Z_]+\b|#include\s+"py/(?!mpconfig\.h")')


def _code(text):
    """The text with its comments taken out."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


@pytest.mark.parametrize("name", DRIVERS)
def test_a_driver_source_names_nothing_of_the_vm(name):
    with open(os.path.join(NET, name)) as f:
        code = _code(f.read())
    hit = _VM.search(code)
    assert hit is None, "%s calls into the VM: %r" % (name, hit.group(0))


def test_the_receive_callback_only_latches_a_record_into_the_ring():
    with open(os.path.join(NET, "moy_link.c")) as f:
        code = _code(f.read())
    body = code[code.index("static void on_recv("):]
    body = body[:body.index("\n}\n")]
    calls = set(re.findall(r"\b([a-zA-Z_]\w*)\s*\(", body)) - {"on_recv", "if"}
    assert calls == {"portENTER_CRITICAL", "portEXIT_CRITICAL",
                     "moy_link_ring_put"}, calls


@pytest.mark.parametrize("board", sorted(CONSOLES))
def test_a_console_image_leaves_the_ports_espnow_out(board):
    path = os.path.join(ROOT, "firmware", board, "boards", CONSOLES[board],
                        "mpconfigboard.h")
    with open(path) as f:
        code = _code(f.read())
    assert re.search(r"#define\s+MICROPY_PY_ESPNOW\s+\(0\)", code), board
    assert re.search(r"#define\s+MOY_NET_LINK\s+\(1\)", code), board

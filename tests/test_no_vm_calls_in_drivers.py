"""The links' and input's drivers call no Python (docs/kernel_survival_2026-10.md
sections 6.9 and 4.7): what runs on the WiFi task, or below the VM between frames, is C that
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
DRIVERS = ("moy_http.c", "moy_net_port.c","moy_sync.c", "moy_sync_apply.c", "moy_webhost.c",
           "moy_wifi.c", "moy_link.c", "moy_link.h", "moy_ota.c", "moy_ota.h", "moy_webconsole.c", "moy_gpio.c", "moy_dns.c",
           "moy_net.h", "moy_net_host.c")

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
    assert re.search(r"#define\s+MOY_NET_WIFI\s+\(1\)", code), board


def test_only_the_station_fallback_constructs_the_ports_wlan():
    """The consoles' station is the kernel's driver; device_wifi.kernel_wlan is
    the one place the port's network.WLAN may appear, as the Zero's fallback."""
    import glob
    hits = []
    for path in glob.glob(os.path.join(ROOT, "device", "*.py")) + \
            glob.glob(os.path.join(ROOT, "runtime", "*.py")):
        with open(path) as f:
            for n, line in enumerate(f, 1):
                if "network.WLAN(" in line and not line.lstrip().startswith("#"):
                    hits.append("%s:%d" % (os.path.relpath(path, ROOT), n))
    assert [h.split(":")[0] for h in hits] == ["device/device_wifi.py"], hits


# The kernel's audio (section 5.5): the session table, the mix and the feeder
# task, and the codec's register sequence.
AUDIO = (os.path.join(ROOT, "native", "moy_audio", n)
         for n in ("moy_aud.c", "moy_aud_out.c", "moy_codec_es8311.c"))


@pytest.mark.parametrize("path", sorted(AUDIO))
def test_the_audio_feed_names_nothing_of_the_vm(path):
    with open(path) as f:
        code = _code(f.read())
    hit = _VM.search(code)
    assert hit is None, "%s calls into the VM: %r" % (path, hit.group(0))



# -- input's drivers (native/moy_input) ----------------------------------------------
#
# The table, the drivers, the input task with its ISRs and the BLE central run
# below the VM and outlive it. The one VM file in the directory is the binding,
# modmoy_input.c, which runs on the VM's task.

INPUT = os.path.join(ROOT, "native", "moy_input")
INPUT_BINDING = {"modmoy_input.c", "fuzz_input.c"}
INPUT_DRIVERS = ("moy_input.c", "moy_input.h", "moy_kbd.c", "moy_touchdev.c", "moy_touch.c",
                 "moy_touch.h", "moy_drivers.h", "moy_input_task.c", "moy_hid.c", "moy_hid.h",
                 "moy_ble_task.c", "moy_ble.h")


def test_every_input_source_but_the_binding_is_checked():
    names = {n for n in os.listdir(INPUT) if n.endswith((".c", ".h"))} - INPUT_BINDING
    assert names == set(INPUT_DRIVERS), names


@pytest.mark.parametrize("name", INPUT_DRIVERS)
def test_an_input_driver_names_nothing_of_the_vm(name):
    with open(os.path.join(INPUT, name)) as f:
        code = _code(f.read())
    hit = _VM.search(code)
    assert hit is None, "%s calls into the VM: %r" % (name, hit.group(0))

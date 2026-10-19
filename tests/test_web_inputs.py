"""The browser declares its inputs as a board does (docs/ui_api_2026-10.md,
"Hints"): a phone's page (`(pointer: coarse)`, which page_tail.js tells the
worker at init) is on touch from boot, a key brings the keys back and the
page's pointer press puts touch back in use; a fine pointer is a mouse, no
finger, and its page stays on its keys."""

import os
import sys
from pathlib import Path

import pytest

from runtime import ui_draw
from ws_helpers import build_ws, make_drv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def web_boot():
    """web_boot imported for its declarations and its pointer sink -- no VM,
    no canvas (as tests/test_moy_sync.py imports it)."""
    for p in ("firmware/web_runner", "device", "runtime"):
        d = os.path.join(ROOT, p)
        if d not in sys.path:
            sys.path.insert(0, d)
    try:
        import web_boot
    except Exception as exc:  # noqa: BLE001 -- a wasm-only dep would skip, not fail
        pytest.skip("web_boot not importable on the host: %s" % exc)
    yield web_boot
    ui_draw.input_kind("keys")


def test_a_coarse_pointer_page_starts_on_touch_and_a_key_brings_the_keys(web_boot,
                                                                         tmp_path):
    ws = build_ws(Path(tmp_path))
    drv = make_drv(ws)
    ws.use_inputs(web_boot.WEB_TOUCH_INPUTS)
    assert ui_draw.input_kind() == "touch"
    drv.press("down")
    drv.frame(1 / 30)
    assert ui_draw.input_kind() == "keys"
    sink = web_boot._PointerSink(drv)
    sink.down = True
    assert ui_draw.input_kind() == "touch", "the page's pointer is a finger"


def test_a_fine_pointer_page_stays_on_its_keys(web_boot, tmp_path):
    ws = build_ws(Path(tmp_path))
    drv = make_drv(ws)
    ws.use_inputs(web_boot.WEB_INPUTS)
    web_boot._PointerSink(drv).click = True
    assert ui_draw.input_kind() == "keys", "a mouse is no finger"

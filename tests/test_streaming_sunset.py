"""The 2026-08 streaming sunset's absence pins (moycore plan 3.2, 5, 6).

Both waves are now in. The DIE-NOW wave deleted the device web view (TeeCanvas
+ the frame push + device_webview.py), the host web console
(tools/web_console.py), the Settings WEB VIEW surface, and the decline-the-Tee
guards. STAGE 4 finished the job: the wasm head rasterizes with the boards' own
kernel, so the recording stack it had been keeping alive -- CommandCanvas,
DrawRecorder, RecordingLayer, ServedState, SurfaceDelta, WsClientState, the
wire protocol and the page's JS replayer -- is deleted outright, along with
runtime/web_view.py and runtime/web_view_page.py themselves.

The plan's lane ledger pins each deletion with a grep-test so a revert or
cargo-cult reintroduction fails loudly instead of resurrecting a seam the
architecture buried. What SURVIVES by decision: moy_webserver's transport core
and the web_view_ws framing leaf (the 3.4 sync RPC rides both), and
runtime/web_input.py -- the browser event decode, which is transport-shaped
rather than raster-shaped and which that same RPC speaks.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TDECK = os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_micropython")


def _read(*rel):
    with open(os.path.join(ROOT, *rel), encoding="utf-8") as f:
        return f.read()


def test_the_recording_stack_is_gone_with_its_modules():
    """Stage 4: the wasm head re-rasters, so the last consumer of the recorder
    died and both modules went with it."""
    assert not os.path.exists(os.path.join(ROOT, "runtime", "web_view.py"))
    assert not os.path.exists(os.path.join(ROOT, "runtime", "web_view_page.py"))
    assert not os.path.exists(os.path.join(ROOT, "tests", "webharness.py"))
    # The classes must not reappear anywhere in the shared console either.
    names = ("class TeeCanvas", "class CommandCanvas", "class DrawRecorder",
             "class RecordingLayer", "class ServedState", "class SurfaceDelta",
             "class WsClientState")
    rt = os.path.join(ROOT, "runtime")
    for fn in sorted(os.listdir(rt)):
        if not fn.endswith(".py"):
            continue
        src = _read("runtime", fn)
        for n in names:
            assert n not in src, "%s reappeared in runtime/%s" % (n, fn)


def test_the_input_decode_survives_on_its_own():
    """The one piece of the old web_view that is NOT raster: browser events ->
    console input, which the 3.4 sync RPC speaks too."""
    src = _read("runtime", "web_input.py")
    assert "def apply_events" in src
    assert "def apply_ws_text" in src
    assert "def effective_input_kinds" in src
    assert "BUTTON_NAMES" in src


def test_the_wasm_head_rasterizes_with_the_boards_kernel():
    """Stage 4's positive claim: the browser draws its own pixels, with the
    same module and the same canvas class the device runs -- not a third
    raster written for the web."""
    build = _read("firmware", "web_runner", "build.sh")
    assert "native/moy_gfx" in build           # the kernel is compiled in
    assert "device_canvas.py" in build         # ...and the boards' canvas staged
    canvas = _read("firmware", "web_runner", "web_canvas.py")
    assert "from device_canvas import DeviceCanvas" in canvas
    boot = _read("firmware", "web_runner", "web_boot.py")
    assert "web_canvas.make_canvas" in boot
    assert "def fb_addr" in boot               # pixels leave by address, not JSON


def test_host_web_console_is_gone():
    assert not os.path.exists(os.path.join(ROOT, "tools", "web_console.py"))
    assert not os.path.exists(os.path.join(ROOT, "tools", "moybyte-webview.service"))
    assert not os.path.exists(os.path.join(ROOT, "deploy", "setup-web-console.sh"))


def test_device_webview_controller_is_gone():
    assert not os.path.exists(os.path.join(TDECK, "modules", "device_webview.py"))
    runtime_src = _read("firmware", "lilygo_t_deck_plus_micropython",
                        "modules", "moy_runtime.py")
    assert "device_webview" not in runtime_src.replace(
        "# 2026-08 streaming sunset (docs/moycore_plan_2026-08.md 3.2): device_webview.py,", "")
    assert "WebView(" not in runtime_src
    assert "web_hook" not in runtime_src


def test_device_webserver_is_transport_core_only():
    # Load-bearing patterns, not prose (the module header narrates the sunset).
    src = _read("firmware", "lilygo_t_deck_plus_micropython",
                "modules", "moy_webserver.py")
    for dead in ("_wv.TeeCanvas", "_wv.DrawRecorder", "_wv.ServedState",
                 "_wv.SurfaceDelta", "_wv.WsClientState", "def _push_frame",
                 "def recording_wanted", "def stream_mode", "def begin_frame",
                 "PAGE_HTML,", "self.recorder", "self.provider"):
        assert dead not in src, dead
    for alive in ("def parse_request", "def http_response", "class _WSConn",
                  "class WebServer", "def handle_http", "def send_text"):
        assert alive in src, alive


def test_console_has_no_web_hook_surface():
    console = _read("runtime", "console.py")
    assert "web_hook" not in console
    assert "_toggle_web_view" not in console
    settings = _read("runtime", "settings_layer.py")
    assert '"web"' not in settings
    assert "WEB VIEW" not in settings


def test_lua_glue_has_no_tee_sniff():
    src = _read("firmware", "lilygo_t_deck_plus_micropython",
                "modules", "moy_lua_glue.py")
    assert 'getattr(canvas, "_r"' not in src
    assert "is_tee" not in src


def test_boards_no_longer_freeze_the_recording_stack():
    # Pin the cp STAGING lines, not prose (the T-Deck comment narrates this).
    tdeck = _read("firmware", "lilygo_t_deck_plus_micropython", "build.sh")
    assert 'cp "${REPO_ROOT}/runtime/web_view_ws.py"' in tdeck   # framing survives
    assert 'cp "${REPO_ROOT}/runtime/web_view.py"' not in tdeck
    assert 'cp "${REPO_ROOT}/runtime/web_view_page.py"' not in tdeck
    p4 = _read("firmware", "esp32_p4_wifi6_touch_lcd_7b", "build.sh")
    assert "web_view" not in p4

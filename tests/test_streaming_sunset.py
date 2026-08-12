"""The 2026-08 streaming sunset's absence pins (moycore plan 3.2, 5).

The die-now wave deleted the device web view (TeeCanvas + the frame push +
device_webview.py), the host web console (tools/web_console.py), the Settings
WEB VIEW surface, and the decline-the-Tee guards. The plan's lane ledger pins
each deletion with a grep-test so a revert or cargo-cult reintroduction fails
loudly instead of resurrecting a seam the architecture buried. What SURVIVES
by decision: moy_webserver's transport core (the 3.4 sync RPC rides it), and
the recording stack (CommandCanvas/DrawRecorder/RecordingLayer/SurfaceDelta/
WsClientState/the page) as the WASM HEAD's substrate until stage 4.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TDECK = os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_micropython")


def _read(*rel):
    with open(os.path.join(ROOT, *rel), encoding="utf-8") as f:
        return f.read()


def test_tee_canvas_is_gone_from_the_shared_module():
    src = _read("runtime", "web_view.py")
    assert "class TeeCanvas" not in src
    # ...while the wasm head's substrate survives until stage 4.
    assert "class CommandCanvas" in src
    assert "class DrawRecorder" in src
    assert "class RecordingLayer" in src
    assert "class SurfaceDelta" in src
    assert "class WsClientState" in src


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

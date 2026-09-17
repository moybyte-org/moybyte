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
architecture buried. What SURVIVES by decision: moy_webserver's HTTP transport
core, and runtime/web_input.py -- the browser event decode, which is
transport-shaped rather than raster-shaped.

THE WEBSOCKET HALF WENT IN 2026-09, and it is pinned here as an absence for the
same reason everything else on this page is. It survived the sunset itself on
one claim -- "the 3.4 sync RPC rides both" -- and the RPC then shipped as plain
HTTP (`moy_webhost.handle_http`, `runtime/moy_sync`). So the RFC 6455 upgrade,
the framing leaf `runtime/web_view_ws.py`, the persistent `_WSConn` and the
`on_text`/`send_text` seams were ~350 lines frozen into five board images with
no caller of any kind, and `.claude/rules/web.md` had already recorded the
opposite decision on the merits: the update routes go through the HTTP host
"never the idle WebSocket core", because `WS_IDLE_MS` reaped a client through a
flash write.
"""

import os
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TDECK = os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_mainline")


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
    # ...and the boards' canvas staged -- declared in the runner's board.toml
    # since 2026-08-17 (the last hand-rolled staging list to convert).
    from tools import board_config
    staged = board_config.staged_modules(
        os.path.join(ROOT, "firmware", "web_runner"), ROOT)
    assert "device_canvas.py" in staged
    canvas = _read("firmware", "web_runner", "web_canvas.py")
    # The CLAIM is "the browser draws with the boards' own class", not "the
    # import statement is spelled on one line" -- this pinned the latter and
    # broke when the import grew a second name and wrapped.
    assert "from device_canvas import" in canvas
    # Since the SystemCanvas unification the browser's system surface IS the
    # boards' shared class, subclassed only to pin RETAINED_FRAMES.
    assert "class WebSystemCanvas(SystemCanvas)" in canvas
    boot = _read("firmware", "web_runner", "web_boot.py")
    assert "web_canvas.make_canvas" in boot
    assert "def fb_addr" in boot               # pixels leave by address, not JSON


def test_host_web_console_is_gone():
    assert not os.path.exists(os.path.join(ROOT, "tools", "web_console.py"))
    assert not os.path.exists(os.path.join(ROOT, "tools", "moybyte-webview.service"))
    assert not os.path.exists(os.path.join(ROOT, "deploy", "setup-web-console.sh"))


def test_device_webview_controller_is_gone():
    assert not os.path.exists(os.path.join(TDECK, "modules", "device_webview.py"))
    runtime_src = _read("firmware", "lilygo_t_deck_plus_mainline", "modules", "moy_runtime.py")
    assert "device_webview" not in runtime_src.replace(
        "# 2026-08 streaming sunset (docs/history/moycore_plan_2026-08.md 3.2): device_webview.py,", "")
    assert "WebView(" not in runtime_src
    assert "web_hook" not in runtime_src


def test_device_webserver_is_transport_core_only():
    # Load-bearing patterns, not prose (the module header narrates the sunset).
    src = _read("device", "moy_webserver.py")
    for dead in ("_wv.TeeCanvas", "_wv.DrawRecorder", "_wv.ServedState",
                 "_wv.SurfaceDelta", "_wv.WsClientState", "def _push_frame",
                 "def recording_wanted", "def stream_mode", "def begin_frame",
                 "PAGE_HTML,", "self.recorder", "self.provider"):
        assert dead not in src, dead
    for alive in ("def parse_request", "def http_response", "class WebServer",
                  "def handle_http"):
        assert alive in src, alive


def test_the_websocket_half_is_gone_with_its_framing_leaf():
    """The 2026-09 deletion (see this module's docstring): the RPC it was kept
    for speaks plain HTTP, so nothing in the tree ever opened it."""
    assert not os.path.exists(os.path.join(ROOT, "runtime", "web_view_ws.py"))
    # Load-bearing patterns, not prose: the module header narrates this
    # deletion too, so a bare "web_view_ws" would fail on the sentence that
    # records it.
    src = _read("device", "moy_webserver.py")
    for dead in ("class _WSConn", "def _upgrade_ws", "def _service_ws",
                 "def send_text", "def connected", "on_text=", "self.on_text",
                 "import web_view_ws", "WS_OP_TEXT =", "WS_IDLE_MS ="):
        assert dead not in src, dead


def test_no_board_freezes_the_websocket_framing_any_more():
    from tools.board_config import staged_modules

    root = Path(__file__).resolve().parent.parent
    for board in sorted(p.name for p in (root / "firmware").iterdir()
                        if (p / "board.toml").exists()):
        staged = staged_modules(root / "firmware" / board, root)
        assert "web_view_ws.py" not in staged, board


def test_console_has_no_web_hook_surface():
    """The STREAMING web view's surface, specifically.

    Settings grew a "WEB CONSOLE" row on 2026-08-14 and that is not a
    regression here: it is the opposite design. The stream pushed PIXELS over
    the wire and its defect class was cache agreement across a lossy transport;
    the new row hands a browser the wasm console ONCE and then only cart data
    crosses (moycore plan 3.4, moy_webhost.py). Different key, different label,
    and none of the machinery below.

    So these assertions stay narrow on purpose -- `"web"` as a whole settings
    key and the exact "WEB VIEW" label -- rather than banning the substring
    "web", which would forbid the successor along with the thing being buried.
    """
    console = _read("runtime", "console.py")
    assert "web_hook" not in console
    assert "_toggle_web_view" not in console
    settings = _read("runtime", "settings_layer.py")
    assert '"web"' not in settings
    assert "WEB VIEW" not in settings


def test_lua_glue_has_no_tee_sniff():
    # The glue that carried the sniff is gone entirely -- the second Lua
    # runtime went with the deletion of LuaCartRun -- so the claim is now
    # about the one that replaced it.
    root = Path(__file__).resolve().parent.parent
    assert not (root / "device" / "moy_lua_glue.py").exists()
    src = _read("device", "moycore_glue.py")
    assert 'getattr(canvas, "_r"' not in src
    assert "is_tee" not in src


def test_boards_no_longer_freeze_the_recording_stack():
    # Pin the STAGED SET, not prose and not shell syntax. Since #161 Phase 3 the
    # boards stage every runtime/*.py minus their board.toml denylist, so the
    # question this test always meant -- "is the recorder frozen onto a board?"
    # -- is answered by asking what a fresh build stages.
    #
    # The ban is on the DELETED modules BY NAME, never on the bare substring
    # "web_view": banning that once banned the framing leaf the transport then
    # imported, which is how the P4 lost it in 06506ab and shipped a web console
    # that could not import for two days. The leaf is gone on its own terms now
    # (the test above), and the lesson about how to write the ban is not.
    from tools.board_config import staged_modules

    root = Path(__file__).resolve().parent.parent
    for board in ("lilygo_t_deck_plus_mainline",
                  "esp32_p4_wifi6_touch_lcd_7b"):
        staged = staged_modules(root / "firmware" / board, root)
        assert "web_view.py" not in staged, board
        assert "web_view_page.py" not in staged, board

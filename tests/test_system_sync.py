"""The console's own documents between a board and the page it serves.

A board-served page reads the board's portable Settings and badges through
GET /console.json and sends its changes back as system batches; the board's C
parks them for the VM (native/moy_net/moy_webhost.c), which merges them key by
key (runtime/moy_sync.py's SYSTEM_KEYS, merge_system, SystemReceiver). The
board is of record, so most of what is pinned here is what a page can NOT do:
change a key the board keeps for itself, turn on unsigned code, read the pin,
or take a badge away. The real-browser round trip is
tests/test_web_board_system_e2e.py.
"""

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "device"))

import moy_webhost as wh                                       # noqa: E402
from runtime import moy_sync, net_binding as nb               # noqa: E402
from runtime.moy_spine import Settings                        # noqa: E402
from runtime.widgets import Achievements                      # noqa: E402

PORTABLE, LOCAL, BOARD = moy_sync.PORTABLE, moy_sync.LOCAL, moy_sync.BOARD


@pytest.fixture(autouse=True)
def _kernel_host():
    nb._bake({})
    yield
    nb.web_stop()
    nb.web_events()
    nb._bake({})


class _Rows(Settings):
    """Settings whose save hook counts the writes."""

    def __init__(self, doc=None):
        self.saves = []
        super().__init__(lambda text: self.saves.append(text) or True)
        if doc:
            self.adopt(doc)


BOARD_DOC = {
    "theme": "dusk", "theme_variant": "dark", "name": "Mia", "author": "mia",
    "show_fps": True, "volume": 2, "font_scale": 2, "web_pin": "4821",
    "ota_channel": "stable", "unknown_sources": False,
    "app_guard": {"local.calc": [3, 0]},
    "favorites": ["/sd/moybyte/carts/moybyte.star_catcher.moy"],
    "paint_doc": "sunset.moyimg", "retired_thing": 7,
}


# -- the table ----------------------------------------------------------------


@pytest.mark.parametrize("key,cls", [
    ("theme", PORTABLE), ("theme_variant", PORTABLE),
    ("layout_theme", PORTABLE), ("name", PORTABLE), ("author", PORTABLE),
    ("steady", PORTABLE), ("show_fps", PORTABLE), ("favorites", PORTABLE),
    ("plays", PORTABLE), ("desk_mru", PORTABLE),
    ("paint_doc", PORTABLE), ("paint_doc_kind", PORTABLE),
    ("local.notes_draft", PORTABLE), ("moybyte.calc_mem", PORTABLE),
    ("volume", LOCAL), ("brightness", LOCAL), ("idle_dim", LOCAL),
    ("idle_saver", LOCAL), ("idle_blank", LOCAL), ("font_scale", LOCAL),
    ("chrome_scale", LOCAL), ("crisp_pixels", LOCAL), ("two_player", LOCAL),
    ("diag_live", LOCAL), ("diag_sd", LOCAL), ("app_guard", LOCAL),
    ("wallpaper_guard", LOCAL),
    ("web_pin", BOARD), ("ota_channel", BOARD), ("unknown_sources", BOARD),
    ("frameskip", LOCAL), ("never_heard_of_it", LOCAL), ("paint_", LOCAL),
])
def test_every_key_has_its_class_and_an_unknown_one_is_local(key, cls):
    assert moy_sync.key_class(key) == cls


def test_the_author_key_is_the_stores_own():
    from runtime.moy_store_base import USER_NS_KEY
    assert moy_sync.SYSTEM_KEYS[USER_NS_KEY][0] == PORTABLE


def test_every_settings_toggle_is_classified():
    """A toggle added to SETTINGS_TOGGLES without a SYSTEM_KEYS entry would be
    LOCAL by default -- safe, but a decision nobody made."""
    from runtime.settings_layer import SETTINGS_TOGGLES
    for key, *_ in SETTINGS_TOGGLES:
        assert key in moy_sync.SYSTEM_KEYS, key


def test_every_system_app_that_keeps_prefs_has_its_namespace_listed():
    """A system app's prefs rows are `<ns>_<key>`, its ns its PREFS_NS or its
    id: one that takes the prefs role must be named in SYSTEM_PREFS_NS, or its
    rows stay on each console without anybody having decided so."""
    import importlib
    from runtime.app_decls import APPS
    for app in APPS:
        mod, cls = app["entry"].split(":")
        k = getattr(importlib.import_module("runtime." + mod), cls)
        if "prefs" in tuple(getattr(k, "NEEDS", ()) or ()):
            ns = getattr(k, "PREFS_NS", None) or app["id"]
            assert ns in moy_sync.SYSTEM_PREFS_NS, (app["id"], ns)


# -- the pull ------------------------------------------------------------------


def test_the_pull_carries_the_portable_rows_and_never_the_boards_own():
    rows = _Rows(BOARD_DOC)
    pull = moy_sync.system_pull(rows, ["first_open", "first_run"])
    assert set(pull) == {"system.json", "achievements.json"}
    sent = json.loads(pull["system.json"])
    assert sent == {
        "theme": "dusk", "theme_variant": "dark", "name": "Mia",
        "author": "mia", "show_fps": True, "paint_doc": "sunset.moyimg",
        "favorites": ["moybyte.star_catcher.moy"]}
    for key in ("web_pin", "ota_channel", "unknown_sources", "volume",
                "app_guard", "retired_thing"):
        assert key not in sent
    assert json.loads(pull["achievements.json"]) == {
        "unlocked": ["first_open", "first_run"]}


def test_a_pulled_document_lands_under_the_pages_carts_root():
    text = json.dumps({"favorites": ["a.moy", "/x/carts/b.moy", "", ".."],
                       "plays": {"a.moy": [3, 20261020]},
                       "desk_mru": ["b.moy"], "theme": "dusk"})
    doc = json.loads(moy_sync.rebase_system(text, "/moy/carts"))
    assert doc == {"favorites": ["/moy/carts/a.moy", "/moy/carts/b.moy"],
                   "plays": {"/moy/carts/a.moy": [3, 20261020]},
                   "desk_mru": ["/moy/carts/b.moy"], "theme": "dusk"}
    assert moy_sync.rebase_system("[1]", "/moy/carts") is None
    assert moy_sync.rebase_system("{", "/moy/carts") is None


# -- the merge -------------------------------------------------------------------


def _merge(doc, board=None, root="/sd/moybyte/carts"):
    rows = _Rows(BOARD_DOC if board is None else board)
    changed = moy_sync.merge_system(rows, json.dumps(doc), root)
    return rows, changed


def test_a_portable_change_lands_and_reports_its_key():
    page = json.loads(moy_sync.system_pull(_Rows(BOARD_DOC), [])["system.json"])
    page["theme"] = "night"
    rows, changed = _merge(page)
    assert changed == ["theme"]
    assert rows.get("theme") == "night"
    assert len(rows.saves) == 1, "one write for the batch"


def test_an_unchanged_document_changes_nothing_and_writes_nothing():
    page = json.loads(moy_sync.system_pull(_Rows(BOARD_DOC), [])["system.json"])
    rows, changed = _merge(page)
    assert changed == [] and rows.saves == []
    assert json.loads(rows.dump()) == BOARD_DOC


def test_a_page_cannot_change_a_key_the_board_keeps():
    page = dict(BOARD_DOC, volume=7, font_scale=1, idle_blank=0,
                app_guard={}, two_player=True, never_heard_of_it=1)
    rows, changed = _merge(page)
    assert changed == []
    for key in ("volume", "font_scale", "app_guard"):
        assert rows.get(key) == BOARD_DOC[key]
    for key in ("idle_blank", "two_player", "never_heard_of_it"):
        assert rows.get(key) is None


def test_a_page_can_never_turn_on_unsigned_code_or_move_the_pin():
    """The board-only keys: a page that sends UNKNOWN SOURCES on, a pin of
    its own and the unstable channel changes none of them."""
    page = dict(BOARD_DOC, unknown_sources=True, web_pin="0000",
                ota_channel="unstable")
    rows, changed = _merge(page)
    assert rows.get("unknown_sources") is False
    assert rows.get("web_pin") == "4821"
    assert rows.get("ota_channel") == "stable"
    assert "unknown_sources" not in changed
    # ...and a board that never had the row does not gain it either.
    rows, _ = _merge({"unknown_sources": True, "theme": "dusk"},
                     board={"theme": "dusk"})
    assert rows.get("unknown_sources") is None


def test_a_value_of_the_wrong_shape_is_passed_over():
    rows, changed = _merge(dict(BOARD_DOC, show_fps="yes", theme=3,
                                favorites="all"))
    assert changed == []
    assert rows.get("show_fps") is True and rows.get("theme") == "dusk"


def test_a_portable_key_the_page_dropped_is_dropped_here():
    page = json.loads(moy_sync.system_pull(_Rows(BOARD_DOC), [])["system.json"])
    del page["paint_doc"]
    rows, changed = _merge(page)
    assert changed == ["paint_doc"] and rows.get("paint_doc") is None
    assert rows.get("web_pin") == "4821", "a key the page never had stays"


def test_an_empty_document_says_nothing():
    rows, changed = _merge({})
    assert changed == [] and json.loads(rows.dump()) == BOARD_DOC


def test_cart_paths_land_under_the_boards_own_root():
    rows, changed = _merge(dict(
        BOARD_DOC, favorites=["/moy/carts/moybyte.star_catcher.moy",
                              "/moy/carts/local.hop.moy"],
        plays={"/moy/carts/local.hop.moy": [2, 20261020]}))
    assert sorted(changed) == ["favorites", "plays"]
    assert rows.get("favorites") == [
        "/sd/moybyte/carts/moybyte.star_catcher.moy",
        "/sd/moybyte/carts/local.hop.moy"]
    assert rows.get("plays") == {"/sd/moybyte/carts/local.hop.moy": [2, 20261020]}


def test_a_document_that_is_not_an_object_is_refused():
    rows = _Rows(BOARD_DOC)
    for text in ("[]", "3", "{"):
        with pytest.raises(ValueError):
            moy_sync.merge_system(rows, text, "/c")


# -- the badges ---------------------------------------------------------------------


def test_badges_are_a_union_never_the_last_writers_list():
    saved = []
    ach = Achievements(["first_run"], on_save=saved.append)
    n = moy_sync.merge_achievements(
        json.dumps({"unlocked": ["first_open", "no_such_badge", 3]}),
        lambda i: ach.award(i, quiet=True))
    assert n == 1
    assert sorted(ach.unlocked) == ["first_open", "first_run"]
    assert saved == [["first_run", "first_open"]]
    assert ach.toast is None, "a badge earned in the page is not celebrated twice"
    # A page whose list lacks a badge the board has takes nothing away.
    assert moy_sync.merge_achievements('{"unlocked": []}', ach.award) == 0
    assert sorted(ach.unlocked) == ["first_open", "first_run"]
    with pytest.raises(ValueError):
        moy_sync.merge_achievements("[]", ach.award)


# -- the receiver -------------------------------------------------------------------


def _rx_apply(ops, rows=None, ach=None, rx=None):
    rows = rows if rows is not None else _Rows(BOARD_DOC)
    ach = ach if ach is not None else Achievements([])
    rx = rx or moy_sync.SystemReceiver()
    return rx.apply(ops, rows, lambda i: ach.award(i, quiet=True),
                    "/sd/moybyte/carts"), rows, ach


def test_the_receiver_takes_the_two_documents_and_refuses_the_rest():
    (applied, errors, changed), rows, ach = _rx_apply([
        {"p": "system.json", "t": json.dumps({"theme": "night"})},
        {"p": "achievements.json", "t": '{"unlocked": ["first_open"]}'},
        {"p": "system.json", "d": 1},
        {"p": "system_icons.moygfx", "b": "AAAA"},
        {"p": "wifi.json", "t": "[]"},
        {"p": "achievements.json", "t": "nope"},
        "junk"], rows=_Rows({"theme": "dusk", "volume": 3}))
    assert applied == 2 and changed == ["theme"]
    assert errors == [(2, "kept on the console"), (3, "stays on the console"),
                      (4, "stays on the console"), (5, "bad document"),
                      (6, "bad op")]
    assert rows.get("theme") == "night" and ach.has("first_open")


def test_a_document_in_parts_lands_at_its_publish_across_batches():
    text = json.dumps(dict(BOARD_DOC, name="Leo"))
    rx = moy_sync.SystemReceiver()
    rows = _Rows(BOARD_DOC)
    (a1, e1, c1), _, _ = _rx_apply([{"p": "system.json", "t": text[:20],
                                     "part": 0}], rows=rows, rx=rx)
    assert (a1, e1, c1) == (1, [], []) and rows.get("name") == "Mia"
    (a2, e2, c2), _, _ = _rx_apply([{"p": "system.json", "t": text[20:],
                                     "part": 1},
                                    {"p": "system.json", "pub": 1}],
                                   rows=rows, rx=rx)
    assert (a2, e2, c2) == (2, [], ["name"]) and rows.get("name") == "Leo"
    (_a, e3, _c), _, _ = _rx_apply([{"p": "system.json", "pub": 1}], rx=rx)
    assert e3 == [(0, "nothing to publish")]
    (_a, e4, _c), _, _ = _rx_apply([{"p": "system.json", "t": "x",
                                     "part": 1}], rx=moy_sync.SystemReceiver())
    assert e4 == [(0, "part out of order")]


# -- the webhost ----------------------------------------------------------------------


class _Ws:
    """What ConsoleSystem reads of a console: its rows, its badges, its carts
    root and the re-apply verb."""

    def __init__(self, doc=None, ids=()):
        self.system = _Rows(BOARD_DOC if doc is None else doc)
        self.ach = Achievements(list(ids))
        self.carts_root = "/sd/moybyte/carts"
        self.applied = []

    def apply_system_keys(self, keys):
        self.applied.append(list(keys))


def _store(tmp_path):
    root = tmp_path / "carts"
    (root / "hop.moy").mkdir(parents=True)
    (root / "hop.moy" / "main.py").write_text("x = 1\n")
    return root


def _host(tmp_path, ws=None, pin="4821", **kw):
    kw.setdefault("port", 0)
    system = None if ws is False else wh.ConsoleSystem(ws or _Ws())
    return wh.WebHost(str(_store(tmp_path)), pin=pin, system=system, **kw)


def _batch(ops, pin="4821", v=2, root="system"):
    doc = {"v": v, "root": root, "ops": ops}
    if pin is not None:
        doc["pin"] = pin
    return json.dumps(doc).encode()


def _status(resp):
    return int(resp.split(b" ", 2)[1])


def _json(resp):
    return json.loads(resp.split(b"\r\n\r\n", 1)[1])


def test_the_pull_needs_the_pin(tmp_path):
    h = _host(tmp_path)
    assert _status(h.handle_http("GET", "/console.json", b"")) == 403
    assert _status(h.handle_http("GET", "/console.json?pin=0000", b"")) == 403
    r = h.handle_http("GET", "/console.json?pin=4821", b"")
    assert _status(r) == 200
    doc = _json(r)
    assert json.loads(doc["system.json"])["theme"] == "dusk"
    assert "4821" not in doc["system.json"], "the pull carried the pin"


def test_a_system_batch_needs_the_pin(tmp_path):
    ws = _Ws()
    h = _host(tmp_path, ws)
    ops = [{"p": "system.json", "t": json.dumps({"theme": "night"})}]
    for pin in (None, "0000"):
        assert _status(h.handle_http("POST", "/sync", _batch(ops, pin))) == 403
    assert ws.system.get("theme") == "dusk" and ws.applied == []


def test_a_system_batch_is_merged_and_its_keys_re_applied(tmp_path):
    ws = _Ws(ids=["first_run"])
    h = _host(tmp_path, ws)
    page = json.loads(moy_sync.system_pull(ws.system, [])["system.json"])
    page.update(theme="night", show_fps=False, unknown_sources=True)
    r = h.handle_http("POST", "/sync", _batch([
        {"p": "system.json", "t": json.dumps(page)},
        {"p": "achievements.json", "t": '{"unlocked": ["first_open"]}'}]))
    assert _status(r) == 200 and _json(r) == {"ok": 2, "err": []}
    assert ws.system.get("theme") == "night"
    assert ws.system.get("show_fps") is False
    assert ws.system.get("unknown_sources") is False
    assert ws.system.get("favorites") == BOARD_DOC["favorites"]
    assert ws.applied == [["theme", "show_fps"]]
    assert sorted(ws.ach.unlocked) == ["first_open", "first_run"]


def test_a_host_without_the_system_root_refuses_its_batch(tmp_path):
    """A board whose VM answers no /console.json (the Zero, whose page keeps
    its Settings in the tab) refuses the batch, never misfiles it; firmware
    from before the root refuses it too, as a batch it cannot parse."""
    h = _host(tmp_path, ws=False)
    ops = [{"p": "system.json", "t": "{}"}]
    r = h.handle_http("POST", "/sync", _batch(ops))
    assert _status(r) == 400 and _json(r) == {"error": "no such store"}
    assert _status(h.handle_http("GET", "/console.json?pin=4821", b"")) == 404
    assert not (tmp_path / "carts" / "system.json").exists()
    assert not (tmp_path / "system.json").exists()


def test_a_carts_batch_still_applies_in_c_beside_the_system_root(tmp_path):
    h = _host(tmp_path)
    r = h.handle_http("POST", "/sync", _batch(
        [{"p": "hop.moy/main.py", "t": "x = 2\n"}], v=1, root=None))
    assert _json(r)["ok"] == 1
    assert (tmp_path / "carts" / "hop.moy" / "main.py").read_text() == "x = 2\n"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _exchange(host, raw, polls=400):
    out = {}

    def client():
        c = socket.create_connection(("127.0.0.1", host.port), timeout=5)
        c.sendall(raw)
        got = b""
        while True:
            k = c.recv(65536)
            if not k:
                break
            got += k
        c.close()
        out["r"] = got

    t = threading.Thread(target=client)
    t.start()
    for _ in range(polls):
        host.poll()
        if not t.is_alive():
            break
        time.sleep(0.005)
    t.join(5)
    assert not t.is_alive(), "the exchange never finished"
    return out["r"]


def test_over_a_socket_the_c_parks_the_batch_for_the_vm(tmp_path):
    """The board's own path: the kernel's poll parks the pinned system batch,
    the VM's poll takes it, merges it and answers on the same connection."""
    ws = _Ws()
    h = _host(tmp_path, ws, port=_free_port())
    h.start(ip="127.0.0.1")
    try:
        body = _batch([{"p": "system.json", "t": json.dumps(
            dict(json.loads(moy_sync.system_pull(ws.system, [])["system.json"]),
                 theme="night"))}])
        raw = (b"POST /sync HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n"
               % len(body)) + body
        r = _exchange(h, raw)
        assert _status(r) == 200 and _json(r) == {"ok": 1, "err": []}
        assert ws.system.get("theme") == "night"
        assert ws.applied == [["theme"]]
        r = _exchange(h, b"GET /console.json?pin=4821 HTTP/1.1\r\nHost: x\r\n\r\n")
        assert json.loads(_json(r)["system.json"])["theme"] == "night"
    finally:
        h.stop()


# -- the console's re-apply ----------------------------------------------------------


def test_a_theme_merged_from_the_page_is_the_consoles_at_once(tmp_path):
    """(a) the live re-apply: a theme the page picked shows on the console
    through the verb boot uses, with the row it came from unwritten again."""
    sys.path.insert(0, str(ROOT / "tests"))
    from ws_helpers import build_ws
    from runtime.chrome import THEME_NAMES
    ws = build_ws(tmp_path)
    other = next(n for n in THEME_NAMES if n != ws.look.theme_name)
    host = wh.ConsoleSystem(ws)
    page = json.loads(host.pull()["system.json"])
    page["theme"] = other
    page["theme_variant"] = "dark"
    applied, errors = host.apply([{"p": "system.json", "t": json.dumps(page)}])
    assert (applied, errors) == (1, [])
    assert ws.look.theme_name == other and ws.look.theme_variant == "dark"
    assert ws.system.get("theme") == other


def test_a_theme_no_console_has_re_applies_as_boot_reads_it(tmp_path):
    """A stored name no theme has reads as absent at boot: the default, in its
    own mode. The live re-apply lands on the same look, so a board's glass
    after a page's change is the one its next boot shows (on a Guition S3,
    2026-10-20, a row naming a retired `night` re-applied in `dark` where boot
    showed `light`)."""
    sys.path.insert(0, str(ROOT / "tests"))
    from ws_helpers import build_ws
    from runtime.chrome import DEFAULT_THEME, DEFAULT_VARIANT
    ws = build_ws(tmp_path)
    ws.system.set("theme", "no_such_theme", persist=False)
    ws.system.set("theme_variant", "dark")
    ws.apply_system_keys(["theme", "theme_variant"])
    live = (ws.look.theme_name, ws.look.theme_variant)
    assert live == (DEFAULT_THEME, DEFAULT_VARIANT)
    assert live == (lambda w: (w.look.theme_name, w.look.theme_variant))(
        build_ws(tmp_path))

"""The crash guard on the WALLPAPER role (#160).

A wallpaper cart runs itself at every boot, so one that hangs or faults the
board is a boot loop unless a mark written BEFORE its code runs outlives the
death. `runtime/crash_guard.py` holds the design; these pin the backdrop's
adoption of it: the strike-out after three boots (a wallpaper that raises, and
one that takes the board down before it paints), survival across fresh
workstations over one store, the fill and the notice a struck-out wallpaper
leaves, the two ways back (a code commit, a deliberate pick), per-role strikes,
and the write count a healthy wallpaper costs a boot.
"""
import json
import sys
from pathlib import Path

from runtime import moy_carts
from runtime.crash_guard import KEY, WALLPAPER_KEY, CrashGuard
from ws_helpers import build_desktop_ws, build_ws, open_cart

DT = 1.0 / 30

# Raises in _init, on every run: the handled shape. The console survives it,
# the backdrop drops to its fill, and nothing ever heals it.
CRASH_SRC = """
def _init():
    raise ValueError("wallpaper always broken")


def _draw():
    cls(1)
"""

# Hangs on its first _update: the shape no except can see. The tests below
# never paint it -- the board dies on that first frame -- and the guard has to
# have written its mark before the compile for the next boot to know.
HANG_SRC = """
def _update(dt):
    while True:
        pass


def _draw():
    cls(2)
"""

CALM_SRC = """
t = 0


def _update(dt):
    global t
    t += dt


def _draw():
    cls(3)
"""

STILL_SRC = """
def _draw():
    cls(4)
"""


def _carts(tmp_path):
    return str(tmp_path / "carts")


def _wallpaper(tmp_path, slug, src, title=None):
    """A wallpaper cart in the store, chosen as the backdrop the next boot
    restores (what a kid's earlier Appearance pick left in system.json)."""
    carts = _carts(tmp_path)
    d = Path(carts) / (slug + ".moy")
    d.mkdir(parents=True, exist_ok=True)
    man = {"title": title or slug.title(), "type": "wallpaper",
           "canvas": "320x240"}
    (d / "manifest.json").write_text(json.dumps(man))
    (d / "main.py").write_text(src)
    (d / "config.json").write_text("{}")
    settings = moy_carts.load_system(carts) or {}
    settings["wallpaper"] = slug
    moy_carts.save_system(settings, carts)
    return d


def _paint(ws, n=CrashGuard.HEAL_FRAMES):
    for _ in range(n):
        ws.input.begin_frame()
        ws.frame(DT)


def _on_card(tmp_path):
    """The wallpaper ledger as the NEXT boot will read it."""
    return (moy_carts.load_system(_carts(tmp_path)) or {}).get(WALLPAPER_KEY, {})


def _count_writes(monkeypatch):
    writes = []
    real = moy_carts.save_system

    def counted(settings, root=moy_carts.CARTS_DIR):
        writes.append(json.loads(json.dumps(settings)))
        return real(settings, root)

    monkeypatch.setattr(moy_carts, "save_system", counted)
    return writes


def _cart(ws, slug):
    return next(c for c in ws.carts.all if ws.look.wp_id_for(c) == slug)


# ---------------------------------------------------------------------------
# the guard itself, on a plain dict
# ---------------------------------------------------------------------------

def test_a_healed_proof_lets_the_next_arm_write_nothing():
    store, saves = {}, []
    g = CrashGuard(store, lambda: saves.append(1), key=WALLPAPER_KEY)
    assert g.arm("sky", "p1") is True
    assert len(saves) == 1 and g.strikes("sky") == 1
    assert g.heal() is True
    assert len(saves) == 2 and g.strikes("sky") == 0
    assert store[WALLPAPER_KEY]["proven"] == {"sky": "p1"}

    assert g.arm("sky", "p1") is True              # proven: no bracket at all
    assert len(saves) == 2
    assert g.strikes("sky") == 0 and g.last_open() is None
    assert g.frame() is False and g.heal() is False

    assert g.arm("sky", "p2") is True              # new code: bracketed again
    assert len(saves) == 3 and g.strikes("sky") == 1


def test_a_struck_out_id_is_refused_whatever_it_proves():
    g = CrashGuard({}, key=WALLPAPER_KEY)
    g.arm("sky", "good")
    g.heal()
    for _ in range(g.STRIKES):
        assert g.arm("sky", "bad") is True
        g.release()
    assert g.arm("sky", "good") is False           # forgiveness is explicit


def test_re_arming_the_held_id_is_the_same_attempt():
    """A recompile in the process that armed it: that run did not kill the
    board, and it did not fail (a failure releases), so no second strike."""
    saves = []
    g = CrashGuard({}, lambda: saves.append(1), key=WALLPAPER_KEY)
    g.arm("sky", "p")
    g.arm("sky", "p")
    assert g.strikes("sky") == 1 and len(saves) == 1
    g.release()
    g.arm("sky", "p")                              # after a failure it counts
    assert g.strikes("sky") == 2


def test_forgiving_the_held_id_makes_the_next_arm_a_fresh_one():
    """A pick of the wallpaper that is armed and not yet healed: the forgive
    clears the mark on the card, so the re-arm has to write it again -- the
    code is about to run a second time."""
    store, saves = {}, []
    g = CrashGuard(store, lambda: saves.append(1), key=WALLPAPER_KEY)
    g.arm("sky", "p")
    g.forgive("sky")
    assert g.last_open() is None
    g.arm("sky", "p")
    assert g.strikes("sky") == 1 and g.last_open() == "sky"
    assert len(saves) == 3


def test_the_two_roles_keep_separate_ledgers():
    store = {}
    apps = CrashGuard(store)
    walls = CrashGuard(store, key=WALLPAPER_KEY)
    for _ in range(walls.STRIKES):
        walls.arm("sky")
        walls.release()
    assert walls.disabled("sky") is True
    assert apps.disabled("sky") is False
    assert apps.arm("sky") is True
    assert store[WALLPAPER_KEY]["strikes"] == {"sky": walls.STRIKES}
    assert store[KEY]["strikes"] == {"sky": 1}


# ---------------------------------------------------------------------------
# the backdrop, boot after boot over one store
# ---------------------------------------------------------------------------

def test_a_wallpaper_that_raises_strikes_out_after_three_boots(tmp_path, capsys):
    _wallpaper(tmp_path, "boom", CRASH_SRC)
    for i in range(CrashGuard.STRIKES):
        capsys.readouterr()
        ws = build_ws(tmp_path)                    # a fresh boot each time
        _paint(ws)
        assert "wallpaper always broken" in capsys.readouterr().out, i
        assert ws.wallpaper_guard.strikes("boom") == i + 1
        assert _on_card(tmp_path)["strikes"]["boom"] == i + 1

    ws = build_ws(tmp_path)
    assert ws.wallpaper_guard.disabled("boom") is True
    assert ws.wallpaper._wp_ns is None
    _paint(ws, 1)
    assert "wallpaper always broken" not in capsys.readouterr().out, \
        "the struck-out wallpaper RAN again"
    assert ws.look.wallpaper_id == "boom"          # the choice is kept
    assert ws._notice is not None
    assert ws._notice[0] == "WALLPAPER OFF"
    assert "Boom" in ws._notice[1]


def test_a_wallpaper_that_takes_the_board_down_strikes_out_across_reboots(
        tmp_path):
    """Armed, never healed, then a reboot: the mark is on the card before the
    compile, so the boot after a hang knows which wallpaper was running."""
    _wallpaper(tmp_path, "spin", HANG_SRC)
    for i in range(CrashGuard.STRIKES):
        ws = build_ws(tmp_path)
        assert ws.wallpaper._wp_update is not None  # compiled, about to hang
        on_card = _on_card(tmp_path)
        assert on_card["strikes"]["spin"] == i + 1
        assert on_card["open"] == "spin"
        # The first frame would hang here and the board would reset.

    ws = build_ws(tmp_path)
    assert ws.wallpaper_guard.disabled("spin") is True
    # Checked BEFORE painting: if the guard let it compile, the frame hangs.
    assert ws.wallpaper._wp_update is None
    assert ws.wallpaper._wp_draw is None
    _paint(ws)
    assert ws._notice is not None and ws._notice[0] == "WALLPAPER OFF"
    assert ws.screen == "launcher"


def test_a_healthy_wallpaper_boots_for_free_once_proven(tmp_path, monkeypatch):
    """Two writes on the boot that proves it (arm, heal), none after -- the
    wallpaper runs at every boot, and a bracket per boot would put two flash
    writes, the cold one included, on every one of them."""
    _wallpaper(tmp_path, "calm", CALM_SRC)
    writes = _count_writes(monkeypatch)

    ws = build_ws(tmp_path)
    assert len(writes) == 1
    assert writes[0][WALLPAPER_KEY]["open"] == "calm"   # written before it ran
    _paint(ws)
    assert len(writes) == 2
    assert ws.wallpaper_guard.strikes("calm") == 0
    assert ws.wallpaper_guard.last_open() is None

    for _ in range(3):
        del writes[:]
        ws = build_ws(tmp_path)
        _paint(ws, 10)
        assert writes == [], "a proven wallpaper wrote on boot"
        assert ws.wallpaper._wp_draw is not None

    # New code is unproven code: the next boot brackets it again.
    (Path(_carts(tmp_path)) / "calm.moy" / "main.py").write_text(
        CALM_SRC + "\n# moved\n")
    del writes[:]
    ws = build_ws(tmp_path)
    assert len(writes) == 1
    _paint(ws)
    assert len(writes) == 2

    # So is a new firmware build.
    wp_mod = sys.modules[type(ws.wallpaper).__module__]
    monkeypatch.setattr(wp_mod, "_build_id", lambda: "another build")
    del writes[:]
    ws = build_ws(tmp_path)
    assert len(writes) == 1


def test_the_windowed_desk_heals_it_too(tmp_path, monkeypatch):
    """The P4 shape, where the desk paints the backdrop through `wm_desk` and a
    write costs the most."""
    _wallpaper(tmp_path, "calm", CALM_SRC)
    ws = build_desktop_ws(tmp_path)
    _paint(ws)
    assert ws.wallpaper_guard.strikes("calm") == 0
    assert ws.wallpaper_guard.last_open() is None
    writes = _count_writes(monkeypatch)
    ws = build_desktop_ws(tmp_path)
    _paint(ws, 10)
    assert writes == []


def test_a_static_wallpaper_heals_on_its_first_frame(tmp_path):
    """Nothing may paint a backdrop with no _update again until a touch, so
    waiting for three frames would leave it armed into the next power-off."""
    _wallpaper(tmp_path, "still", STILL_SRC)
    ws = build_ws(tmp_path)
    assert ws.wallpaper_guard.strikes("still") == 1
    _paint(ws, 1)
    assert ws.wallpaper_guard.strikes("still") == 0
    assert ws.wallpaper_guard.last_open() is None


# ---------------------------------------------------------------------------
# the ways back, and what stays usable meanwhile
# ---------------------------------------------------------------------------

def _strike_out(tmp_path, slug, src):
    _wallpaper(tmp_path, slug, src)
    for _ in range(CrashGuard.STRIKES):
        build_ws(tmp_path)                         # boots that never healed
    ws = build_ws(tmp_path)
    assert ws.wallpaper_guard.disabled(slug) is True
    return ws


def test_committing_fixed_code_forgives_a_struck_out_wallpaper(tmp_path):
    ws = _strike_out(tmp_path, "boom", CRASH_SRC)
    ws.open_in_editor(_cart(ws, "boom"))
    ws.set_menu_view("code")
    ws.screen = "menu"
    ws.editor.set_text(CALM_SRC)
    assert ws.save_code() is True
    assert ws.wallpaper_guard.strikes("boom") == 0
    assert _on_card(tmp_path)["strikes"].get("boom") is None

    ws = build_ws(tmp_path)                        # the fixed code runs again
    assert ws.wallpaper._wp_draw is not None
    _paint(ws)
    assert ws.wallpaper_guard.strikes("boom") == 0
    assert ws.wallpaper_guard.last_open() is None


def test_picking_it_again_is_try_again(tmp_path, capsys):
    ws = _strike_out(tmp_path, "boom", CRASH_SRC)
    capsys.readouterr()
    ws.look.select_wallpaper("boom")               # the Appearance pick
    assert "wallpaper always broken" in capsys.readouterr().out
    assert ws.wallpaper_guard.strikes("boom") == 1  # a fresh three, one spent
    assert ws.wallpaper_guard.disabled("boom") is False


def test_a_struck_out_wallpaper_stays_playable_and_editable(tmp_path):
    """The strikes are the WALLPAPER role's: tapped, the cart runs (and shows
    its own crash), and the picker still offers it for editing."""
    ws = _strike_out(tmp_path, "boom", CRASH_SRC)
    cart = _cart(ws, "boom")
    assert ws.app_guard.strikes("boom") == 0
    assert ws.cart_broken(cart) is False
    titles = [it.get("title") for it in ws._picker_items(ws.carts.all)]
    assert "Boom" in titles
    open_cart(ws, "Boom")
    assert ws.player.cart_error is not None
    assert "wallpaper always broken" in ws.player.cart_error
    assert "turned off" not in ws.player.cart_error

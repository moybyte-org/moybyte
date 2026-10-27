"""Shared workstation-test helpers (2026-08-18).

Some 27 test files each carried their own copy of one or more of these -- a
2-line `_ws`, a 12-line `_open_cart`, the cursor/toast quiesce -- and the
copies had begun to vary in ways nobody chose (three different `_open_cart`
miss behaviours, one of them a silent no-op on whatever the launcher had
selected). The exact duplicates import ONE body now; a file with a genuine
variant (a clock-pinning quiesce, a frame-first driver) keeps its local
wrapper, ideally calling these.
"""

from pathlib import Path

from runtime import host_app


# The fixture theme packages (tests/fixtures/themes/<name>.moy): the seeds
# the 2026-10 look deleted (docs/look_2026-10.md step 27), each standalone
# with no look of its own (so the default's, flat: step 33), for the tests
# that draw a palette theme or a frame style; `mint`, a colours-only package.
FIXTURE_THEMES = Path(__file__).resolve().parent / "fixtures" / "themes"


def fresh_icon_caches(monkeypatch):
    """runtime/iconsets.py's module caches, empty for this test alone: what a
    test loads under an image it substitutes (no icon sets, another board's
    `fonts_data`) leaves with the substitution instead of answering the next
    test in the process -- a pixel set cached as absent drew the bar's Moy
    from the IconSheet in every later test in that xdist worker. The mask
    fonts already loaded stay (a copy of `_FONTS`: a load takes a font slot,
    so it is never repeated), and what the test adds goes with it."""
    from runtime import iconsets
    for name, val in list(vars(iconsets).items()):
        if name.startswith("_") and name.isupper() and type(val) in (dict, list):
            fresh = dict(val) if name == "_FONTS" else type(val)()
            monkeypatch.setattr(iconsets, name, fresh)


def image_without_icon_sets(monkeypatch):
    """The host as an image that carries no icon set's files."""
    from runtime import fontset
    monkeypatch.setattr(fontset, "icon_file", lambda name: None)
    fresh_icon_caches(monkeypatch)


def fixture_theme(name):
    """Fixture package `name`'s theme.json bytes."""
    return (FIXTURE_THEMES / (name + ".moy") / "theme.json").read_bytes()


def install_theme(name):
    """Put fixture package `name` in the loader's install slot (conftest
    drops it after the test), so `theme_colors(name)` and `set_theme(name)`
    reach it."""
    import moy_ui
    from runtime import chrome
    rc = moy_ui.theme_install(fixture_theme(name))[0]
    assert rc == 0, (name, rc)
    chrome.forget_themes()


def fixture_colors(name, variant="dark"):
    """Fixture package `name`'s `theme_colors` in `variant`, the slot left
    empty after."""
    import moy_ui
    from runtime import chrome
    install_theme(name)
    try:
        return dict(chrome.theme_colors(name, variant))
    finally:
        moy_ui.theme_uninstall()
        chrome.forget_themes()


# The default look's two variants, Moy Day and Moy Night: a page the three
# runtimes draw alike is checked under both.
MOY_VARIANTS = ("light", "dark")


def use_theme(ws, name, variant=None, store=False):
    """`ws` on fixture package `name`, in `variant` (None keeps the current
    one): in the install slot, or with `store` copied into its store as a
    theme a kid installed (its fonts from its own folder), which a change
    in Appearance makes `custom` from."""
    if store:
        import shutil
        dst = Path(ws.carts_root) / (name + ".moy")
        if not dst.exists():
            shutil.copytree(FIXTURE_THEMES / (name + ".moy"), dst)
            ws.carts.rescan()
    else:
        install_theme(name)
    ws.look.set_theme(name, persist=False, variant=variant)


def boot_on_fixture(monkeypatch, name="night", variant="dark", store=False):
    """Every workstation the test builds boots on fixture package `name` in
    `variant` (`use_theme`'s `store`): for the suites that pin a palette
    theme's bar, its layout's context X and the 320x240 coordinates under
    the default's look drawn flat. A store that names a theme boots it."""
    real = host_app.build_workstation

    def build(*a, **kw):
        ws = real(*a, **kw)
        if ws.system.get("theme") is None:
            use_theme(ws, name, variant, store)
        return ws
    monkeypatch.setattr(host_app, "build_workstation", build)


def build_ws(tmp_path, **kw):
    """A workstation over a fresh seeded store under tmp_path/carts."""
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


def build_desktop_ws(tmp_path, **kw):
    """The windowed-desktop tier (the P4 shape) the WM suites drive."""
    kw.setdefault("sys_size", (1024, 600))
    kw.setdefault("font_scale", 2)
    kw.setdefault("windowed", True)
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


def select_on_home(ws, title):
    """Bring the cart titled `title` up on the home -- the tab that holds it
    (Games, Mine or Apps, docs/shell_layout_2026-10.md §12), then its card --
    and return it; None when no tab holds it. A list a test set on the
    launcher itself is searched first."""
    for i, c in enumerate(ws.launcher.items):
        if c.get("title") == title:
            ws.launcher.sel = i
            return c
    cart = next((c for c in ws.carts.all if c["title"] == title), None)
    if cart is None or not ws.select_cart(cart):
        return None
    return ws.launcher.selected()


def open_cart(ws, title):
    """Open a seeded cart by TITLE. Games, the kid's carts and apps live in
    the home's tabs; a WALLPAPER leaves them (spec shell_ux_v1.md) but stays
    a real editable cart in the store, so fall back to opening it by
    reference (as ws.open() does). A missing title is an AssertionError,
    never a silent open of whatever the launcher had selected."""
    if select_on_home(ws, title) is not None:
        ws.open()
        return
    cart = next((c for c in ws.carts.all if c["title"] == title), None)
    assert cart is not None, "seed cart not found: " + title
    ws._open_workspace(cart)
    ws.run(ws.project, ws.launcher_layer)


def make_drv(ws):
    return host_app.ConsoleDriver(ws)


def quiesce(ws):
    """Hide the cursor + take down the achievement toast so a rendered frame is
    deterministic chrome. (Suites that also pin the OS bar's live clock keep
    their own extended copy.)

    The DEADLINE is what takes an overlay down (#209 landing B) -- the shell's
    own flat field, not the payload on `ws.ach`, which is read only while that
    deadline is up."""
    ws.pointer.visible = False
    ws._toast_until = 0


def no_unlock_banner(ws):
    """Every badge already found, so nothing a test opens raises the unlock
    banner (achievements_ui), whose card at the top of the glass would cover
    the pixels it reads."""
    from runtime.widgets import ACHIEVEMENTS
    for a in ACHIEVEMENTS:
        ws.ach.unlocked.setdefault(a[0], 0)
    return ws


def build_ws_with_cart(tmp_path, src, title="Cart", type="app", edit=None,
                       editor=False):
    """A workstation over a store holding ONE hand-authored cart, opened.

    `editor=True` lands in the Editor (`open_in_editor`) instead of RUNNING the
    cart. A title the launcher does not carry is an AssertionError, like
    `open_cart`: a select loop that falls through in silence opens whatever the
    launcher had selected, which reads as a passing test against a seed cart."""
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts_dir)
    host_app.moy_carts.create(title, carts_dir, src=src, type=type,
                              edit=edit or [])
    ws = host_app.build_workstation(carts_dir)
    assert select_on_home(ws, title) is not None, \
        "created cart not on the launcher: " + title
    if editor:
        ws.open_in_editor()
    else:
        ws.open()
    return ws


def build_ws_with_shelf(tmp_path, n):
    """A workstation whose launcher holds at least `n` carts -- more than one
    viewport, which is the precondition every shelf-scrolling suite needs --
    topped up through the real store, parked at the head."""
    from runtime import host_app, moy_carts

    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)        # seeds the system carts
    while len(ws.launcher.items) < n:                 # top up with extra carts
        i = len(ws.launcher.items)
        moy_carts.create("Extra %02d" % i, carts_dir,
                         src="def _draw():\n    cls(1)\n", type="app")
        ws.launcher.items = shelf(carts_dir)
    ws.launcher.sel = 0
    ws.launcher.scroll = 0
    return ws


def device_frames(ws, n=1, dt=1 / 30):
    """`n` whole frames in the DEVICE lane: the boards' InputState needs
    `handle_input`/`handle_pointer` driven by hand, where the host driver does
    it for you (`game_pointer`, the tuple a cart's `touch()` reads, is only
    refreshed by `handle_pointer`)."""
    for _ in range(n):
        ws.input.begin_frame()
        ws.handle_input()
        ws.handle_pointer()
        ws.frame(dt)


class StubInput:
    """The cart-facing input with nothing held and nothing pressed -- what
    `make_api` wants from a test with no opinion about buttons."""

    def held(self, n):
        return False

    def steps(self, n):
        return int(bool(self.pressed(n)))

    def pressed(self, n):
        return False


def cover_bytes(value=5, stripes=None):
    """A cart cover (SPEC.md 3.6) in the profile: 128x128 console-palette
    indices, one flat colour `value` -- or, with `stripes`, a second colour on
    every other row, so a reduction or a wrong sample has something to show."""
    from runtime import cover_png
    pix = bytearray([value]) * (cover_png.SIDE * cover_png.SIDE)
    if stripes is not None:
        for y in range(1, cover_png.SIDE, 2):
            pix[y * cover_png.SIDE:(y + 1) * cover_png.SIDE] = (
                bytes([stripes]) * cover_png.SIDE)
    return cover_png.encode_indexed(bytes(pix), cover_png.moy64())


def shelf(root):
    """Every cart under `root` WHOLE, each carrying its store handle ("h") --
    what `moy_carts.scan` reads, through the interface the console holds carts
    by (`moy_catalogue`), so a list a test hands a workstation is one the
    console could have built itself."""
    from runtime import moy_catalogue as cat
    return [c for c in (cat.load(e["h"]) for e in cat.catalogue(str(root)))
            if c]


def select_first_game(ws):
    """Select the first GAME on the launcher and return it. The shelf sorts by
    folder, so `sel = 0` is whatever sorts first -- an app, today."""
    for i, it in enumerate(ws.launcher.items):
        if it.get("path") and it.get("type") == "game":
            ws.launcher.sel = i
            return it
    raise AssertionError("no game cart seeded")


def wallpaper_on_store(carts, wp_id):
    """The store a kid's wallpaper pick leaves (docs/theming_2026-09.md D6):
    `custom`, the default seed's look naming `wp_id` (None: NONE), its fonts
    beside it, and the `theme` row on it -- what the next boot shows."""
    import json
    import moy_ui
    from runtime import moy_carts
    from runtime.theme_custom import font_files, manifest
    rc, src = moy_ui.theme_custom("moy", None, None)
    assert rc == 0
    pkg = json.loads(src)
    pkg["wallpapers"] = {"desk": wp_id} if wp_id is not None else {}
    d = Path(carts) / "custom.moy"
    d.mkdir(parents=True, exist_ok=True)
    (d / "theme.json").write_text(json.dumps(pkg))
    (d / "manifest.json").write_bytes(manifest({"from": "moy"}))
    for f in font_files(pkg.get("fonts")):
        data = moy_ui.font_file(f)
        if data is not None:
            (d / f).write_bytes(data)
    settings = moy_carts.load_system(carts) or {}
    settings["theme"] = "custom"
    moy_carts.save_system(settings, carts)
    return d


def settings_back(ws, drv):
    """Tap Settings' way out on the bar: the back chevron where the theme's
    look draws it, else the bar's context X."""
    bl = ws.bar_layer
    if bl.draws_back("settings"):
        x, y, w, h = bl._zone_rect("settings")
        bx, by = bl.cell("settings", "zone")[0][1][:2]
        drv.click(bx + x + h // 2, by + y + h // 2)
        return
    bar, (x, y, w, h) = bl.cell("settings", "close")
    drv.click(bar[1][0] + x + w // 2, bar[1][1] + y + h // 2)


def settings_tile(ws, key, k):
    """The middle of choice `k`'s tile on the Editor's Settings page (look
    step 30: the cards on the verbs), where a frame draws it."""
    ws.input.begin_frame()
    ws.frame(1 / 30)
    (x, y, w, h), _k = ws.cards_layer.page()._tiles[key][k]
    return x + w // 2, y + h // 2


def settings_box(ws, label):
    """The Settings page's row `label` (a door: Cart info, Files) as its last
    frame drew it."""
    ws.input.begin_frame()
    ws.frame(1 / 30)
    return ws.cards_layer.page().host.box(label)

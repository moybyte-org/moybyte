"""`CartManager` direct (#209 landing C, docs/history/console_architecture_2026-08.md).

The roster is the shell's most-read piece of state, and what the extraction put
at risk is not "does a cart appear" -- the goldens and `test_editor_picker`
already draw that -- but four things a whole-screen hash cannot see:

  * **`all` is REBOUND, never mutated in place.** `ws.system` could stay a plain
    alias forever because `SystemStore.load()` clears and updates the same dict;
    a re-scan builds a NEW list, so the same trick is unavailable and a `ws`
    mirror would go stale on the first browser sync. The tests below hold a
    reference across a re-scan and prove it goes stale, which is exactly why
    every consumer had to migrate in this commit rather than keep an alias.

  * **`apply` has an ORDER.** The covers invalidation must run BEFORE `slim`
    (landing C's first commit found that the hard way; `test_cover_cache` owns
    the icon half), and both grids must be re-derived from the same scan.

  * **the store guard is ONE predicate.** `new`/`dup`/`delete` used to spell out
    `not carts_root or not can_manage` each; they take the shared `StoreHandle`
    now, the same object `SystemStore` and `app_context`'s storage roles hold.

  * **`rescan_carts` is the one surviving forward**, because `moy_webhost`
    captures `lambda: ws.rescan_carts()` at CONSTRUCTION -- a name that resolves
    then and never again is exactly the 3d blind spot the façade ratchet exists
    for, so the shape is pinned here as well as counted there.
"""

import ast
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import cart_manager, console, moy_carts  # noqa: E402
from ws_helpers import build_ws  # noqa: E402


def _mk(root, title, **kw):
    kw.setdefault("src", "def _draw():\n    cls(1)\n")
    return moy_carts.create(title, root, **kw)


def _titles(grid):
    return [it.get("title") for it in grid.items if it.get("path")]


# -- the roster ---------------------------------------------------------------

def test_the_kernel_keeps_no_roster_state(tmp_path):
    """No `ws` mirror, and the old name is gone rather than left as a stale
    second copy -- a mirror could not be kept honest, because `apply` rebinds."""
    ws = build_ws(tmp_path)
    assert not hasattr(ws, "_all_carts")
    assert ws.carts.all
    assert "_all_carts" not in ROOT.joinpath("runtime/console.py").read_text(
        encoding="utf-8")


def test_apply_rebinds_the_list_so_a_captured_reference_goes_stale(tmp_path):
    """The reason there is no alias trick here. A consumer that snapshotted the
    list keeps the OLD roster, which is why every reader had to migrate to
    `ws.carts.all` in the same commit instead of holding a reference."""
    ws = build_ws(tmp_path)
    root = str(ws.carts_root)
    captured = ws.carts.all
    _mk(root, "Latecomer")
    ws.carts.apply(moy_carts.scan(root))
    assert ws.carts.all is not captured
    assert "Latecomer" in [c["title"] for c in ws.carts.all]
    assert "Latecomer" not in [c["title"] for c in captured]


def test_apply_ignores_an_empty_scan(tmp_path):
    """A store that answered with nothing is a failed read, not an empty shelf:
    keeping the old roster is what stops a transient SD hiccup wiping the
    launcher."""
    ws = build_ws(tmp_path)
    before = ws.carts.all
    ws.carts.apply([])
    assert ws.carts.all is before


def test_apply_invalidates_covers_before_slimming_and_re_derives_both_grids(tmp_path):
    """The four steps, in order. `slim` bakes each icon and then DELETES the
    sprite art, so an invalidation after it leaves a slimmed cart with no icon
    and nothing to rebuild one from (test_cover_cache runs that mutant end to
    end); the two grid rebuilds must both read the SAME scan."""
    ws = build_ws(tmp_path)
    order = []
    real_slim = ws.carts.slim
    ws.covers.invalidate_all = lambda: order.append("invalidate")
    ws.carts.slim = lambda: (order.append("slim"), real_slim())[1]
    ws.launcher.set_items = lambda items: order.append(("launcher", len(items)))
    ws.picker.set_items = lambda items: order.append(("picker", len(items)))

    ws.carts.apply(moy_carts.scan(str(ws.carts_root)))
    assert order[0] == "invalidate"
    assert order[1] == "slim"
    assert [o[0] for o in order[2:]] == ["launcher", "picker"]


def test_the_grids_follow_a_create_through_apply(tmp_path):
    ws = build_ws(tmp_path)
    root = str(ws.carts_root)
    _mk(root, "Freshly")
    ws.carts.apply(moy_carts.scan(root))
    assert "Freshly" in _titles(ws.launcher)
    assert "Freshly" in _titles(ws.picker)


# -- rescan + the one forward -------------------------------------------------

def test_rescan_adopts_what_landed_on_the_store_and_marks_dirty(tmp_path):
    """The browser sync's board half: files arrive from outside the console, the
    shelf follows with no reboot. `_dirty` is what makes the frame after it
    paint -- without it the new cart sits on a store nothing repaints."""
    ws = build_ws(tmp_path)
    _mk(str(ws.carts_root), "Synced")
    ws._dirty = False
    ws.carts.rescan()
    assert "Synced" in _titles(ws.launcher)
    assert ws._dirty is True


def test_rescan_is_a_no_op_with_no_store_wired(tmp_path):
    """An embedded boot has neither store nor root; the guard is the shared
    handle's `ready()`, not a hand-spelled pair."""
    ws = build_ws(tmp_path)
    before = ws.carts.all
    ws.carts_store = None
    ws.carts.rescan()
    assert ws.carts.all is before


def test_rescan_keeps_the_old_shelf_when_the_scan_raises(tmp_path):
    ws = build_ws(tmp_path)
    before = ws.carts.all

    class _Boom:
        def __getattr__(self, name):
            def _raise(*a, **kw):
                raise OSError("card gone")
            return _raise

    ws.carts_store = _Boom()
    ws.carts.rescan()
    assert ws.carts.all is before


def test_the_webhosts_on_sync_lambda_still_finds_the_shelf(tmp_path):
    """`moy_webhost` builds `on_sync=lambda: ws.rescan_carts()` when the webhost
    is CONSTRUCTED and calls it per batch -- the name has to keep resolving on
    the console, which is why this one forward stayed. Driven in the webhost's
    own shape rather than by calling the method directly."""
    ws = build_ws(tmp_path)
    on_sync = lambda: ws.rescan_carts()          # noqa: E731 -- the webhost's shape
    _mk(str(ws.carts_root), "Pushed")
    on_sync()
    assert "Pushed" in _titles(ws.launcher)


def test_the_rescan_forward_has_a_fixed_signature():
    """A `*a, **kw` shim allocates a tuple per call (the #63/#66 churn class).
    The façade ratchet counts forwards; this pins the one that has a live
    caller outside the tree's own tests."""
    sig = inspect.signature(console.Workstation.rescan_carts)
    assert list(sig.parameters) == ["self"]


# -- the store guard ----------------------------------------------------------

def test_one_store_handle_backs_prefs_carts_and_the_app_roles(tmp_path):
    """Architecture doc 2a: the (store, root, can_manage, with_sd) 4-tuple was
    re-derived in four bodies plus `app_context._StoreRole`. One object now, and
    the identity check is what stops a second one growing back."""
    ws = build_ws(tmp_path)
    assert ws.carts.store is ws.store
    assert ws.prefs.store is ws.store
    ctx = ws.app_context("probe", ("carts", "files"))
    assert ctx.carts.readable() is True
    assert ctx.files.ready() is True
    ws.can_manage = False
    assert ctx.files.ready() is False          # reads THROUGH ws, per call
    assert ws.store.writable() is False


def test_a_read_only_store_refuses_every_write_verb(tmp_path):
    """`can_manage` is False wherever the carts are baked into the image (the
    web runner's spec player, a device with no SD writes). A refusal is a no-op,
    never a crash and never a partial create."""
    ws = build_ws(tmp_path)
    _mk(str(ws.carts_root), "Second")
    ws.carts.apply(moy_carts.scan(str(ws.carts_root)))
    ws.picker.sel = next(i for i, it in enumerate(ws.picker.items)
                         if it.get("title") == "Second")
    before = list(ws.carts.all)
    ws.can_manage = False
    assert ws.carts.new() is None
    ws.carts.dup()
    ws.carts.delete()
    assert [c["title"] for c in ws.carts.all] == [c["title"] for c in before]


# -- new / dup / delete -------------------------------------------------------

def test_new_creates_a_cart_adopts_it_and_hands_it_back(tmp_path):
    """ONE body where there were two: `new_cart` and the first half of
    `new_cart_and_edit` were the same six lines, and `new_cart` had no caller
    anywhere. The RETURN is what let the second one keep its picker-select +
    open-in-editor tail as kernel policy."""
    ws = build_ws(tmp_path)
    n0 = len(ws.carts.all)
    made = ws.carts.new()
    assert made is not None and made.get("path")
    assert len(ws.carts.all) == n0 + 1
    assert any(c["path"] == made["path"] for c in ws.carts.all)
    assert made["path"] in [it.get("path") for it in ws.picker.items]


def test_new_cart_and_edit_opens_the_cart_new_returned(tmp_path):
    """The kernel half: select the fresh project in the picker, then open it in
    the Editor. It reads the cart off `carts.new()` rather than re-deriving it
    from the roster, which is what keeps the two halves from disagreeing about
    WHICH cart was just made."""
    ws = build_ws(tmp_path)
    ws.new_cart_and_edit()
    assert ws.cart is not None
    assert ws.picker.selected().get("path") == ws.cart["path"]


def test_new_cart_and_edit_stays_put_on_a_read_only_store(tmp_path):
    ws = build_ws(tmp_path)
    ws.can_manage = False
    ws.new_cart_and_edit()
    assert ws.cart is None


def test_dup_reads_the_pickers_selection(tmp_path):
    ws = build_ws(tmp_path)
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path"))
    other = ws.launcher.selected()["path"]
    ws.picker.sel = next(i for i, it in enumerate(ws.picker.items)
                         if it.get("path") and it["path"] != other)
    target = ws.picker.selected()
    ws.carts.dup()
    assert any(c["title"] == target["title"] + " copy" for c in ws.carts.all)


def test_dup_rehydrates_the_source_first(tmp_path):
    """`moy_carts.duplicate` copies src/cfg FROM the dict, and the #66 diet has
    already deleted them off a slimmed cart -- so the rehydrate is what stops a
    duplicate coming out empty."""
    ws = build_ws(tmp_path)
    ws.picker.sel = next(i for i, it in enumerate(ws.picker.items)
                         if it.get("path"))
    target = ws.picker.selected()
    ws.carts.slim()
    assert target.get("lazy") is True and "src" not in target
    ws.carts.dup()
    copy = next(c for c in ws.carts.all
                if c["title"] == target["title"] + " copy")
    assert moy_carts.load(copy["path"]).get("src")


def test_delete_prefers_the_open_cart_over_the_pickers_selection(tmp_path):
    """The sysmenu DELETE CART path: a cart opened from the picker is not
    necessarily still the picker's selection."""
    ws = build_ws(tmp_path)
    root = str(ws.carts_root)
    _mk(root, "Opened")
    _mk(root, "Selected")
    ws.carts.apply(moy_carts.scan(root))
    opened = next(c for c in ws.carts.all if c["title"] == "Opened")
    ws._open_workspace(opened)
    ws.picker.sel = next(i for i, it in enumerate(ws.picker.items)
                         if it.get("title") == "Selected")
    ws.carts.delete()
    titles = [c["title"] for c in ws.carts.all]
    assert "Opened" not in titles and "Selected" in titles


def test_delete_keeps_the_last_cart(tmp_path):
    """A device that deleted its way to an empty shelf has no way back."""
    ws = build_ws(tmp_path)
    only = ws.carts.all[0]
    ws.carts.all = [only]
    ws.picker.sel = next(i for i, it in enumerate(ws.picker.items)
                         if it.get("path") == only.get("path"))
    ws.carts.delete()
    assert ws.carts.all == [only]


# -- the #66 live-set diet ----------------------------------------------------

def test_slim_strips_the_heavy_keys_and_rehydrate_refills_the_same_dict(tmp_path):
    """IN PLACE is the contract: the launcher and the picker hold the same dict,
    so one load fattens every reference at once."""
    ws = build_ws(tmp_path)
    cart = next(c for c in ws.carts.all if c.get("path"))
    ws.carts.slim()
    assert cart["lazy"] is True
    assert not [k for k in cart_manager._HEAVY_CART_KEYS if k in cart]

    same = ws.carts.rehydrate(cart)
    assert same is cart
    assert cart["lazy"] is False and cart.get("src")


def test_slim_bakes_the_grid_icon_while_the_art_is_still_in_ram(tmp_path):
    """The one ordering inside `slim` itself: the icon is baked BEFORE the
    sprite art is deleted, because after that there is nothing to bake from.

    Driven off a FRESH FAT scan, because boot already slimmed the roster and
    `slim` skips a cart that is already `lazy` -- that skip is the reason the
    icon prune keeps exactly the slimmed entries (test_cover_cache owns it)."""
    ws = build_ws(tmp_path)
    ws.carts.all = moy_carts.scan(str(ws.carts_root))     # fat again
    assert not any(c.get("lazy") for c in ws.carts.all)
    drawn = [c for c in ws.carts.all if c.get("sprites")]
    assert drawn, "the seeded roster carries no sprite art -- nothing to prove"
    ws.covers.icons = {}
    ws.carts.slim()
    assert all(c.get("lazy") for c in ws.carts.all if c.get("path"))
    # A REAL blittable, not merely a cache entry: deleting the art first still
    # populates the dict, it just fills it with the None fallback forever.
    assert [c for c in drawn if ws.covers.icons.get(c["path"]) is not None]


def test_reslim_only_touches_a_cart_the_diet_manages(tmp_path):
    """Embedded carts (no path) cannot be reloaded, and an already-slim cart has
    nothing to drop -- re-slimming either would be a permanent loss."""
    ws = build_ws(tmp_path)
    embedded = {"title": "Baked in", "src": "x", "cfg": {}}
    ws.carts.reslim(embedded)
    assert embedded["src"] == "x" and "lazy" not in embedded

    already = {"title": "Slim", "path": "p", "lazy": True}
    ws.carts.reslim(already)
    assert already["lazy"] is True


def test_rehydrate_leaves_an_embedded_cart_alone(tmp_path):
    ws = build_ws(tmp_path)
    embedded = {"title": "Baked in", "src": "x"}
    assert ws.carts.rehydrate(embedded) is embedded
    assert "lazy" not in embedded


# -- favorites + recents (#105) -----------------------------------------------

def test_the_launcher_takes_the_collaborators_bound_method(tmp_path):
    """Per card per painted home frame. Not a lambda and not a console forward:
    the grid calls `CartManager.is_favorite` itself, so the star badge costs one
    call rather than a call plus a hop."""
    ws = build_ws(tmp_path)
    fn = ws.launcher.favorite_for
    assert fn.__self__ is ws.carts
    assert fn.__func__ is cart_manager.CartManager.is_favorite
    assert ws.picker.favorite_for is None       # only the RUN grid plays


def test_toggling_a_favorite_persists_through_prefs(tmp_path):
    """The star badge rides system.json's ONE persist funnel -- no new store
    surface, and it survives a reboot."""
    ws = build_ws(tmp_path)
    cart = next(c for c in ws.carts.all if c.get("path"))
    assert ws.carts.is_favorite(cart) is False
    ws.carts.toggle_favorite(cart)
    assert ws.carts.is_favorite(cart) is True
    assert moy_carts.load_system(str(ws.carts_root))["favorites"] == [cart["path"]]

    ws.carts.toggle_favorite(cart)
    assert moy_carts.load_system(str(ws.carts_root))["favorites"] == []


def test_a_pseudo_tile_is_never_starred_or_recorded(tmp_path):
    """The pinned Make / + New tiles carry no path, and a path is the identity
    everything here keys on."""
    ws = build_ws(tmp_path)
    make = ws.launcher.items[0]
    assert make.get("path") is None
    assert ws.carts.is_favorite(make) is False
    ws.carts.toggle_favorite(make)
    ws.carts.note_recent(make)
    ws.carts.note_recent(None)
    assert ws.system.get("favorites", []) == []
    assert ws.system.get("desk_mru", []) == []


def test_recents_move_to_the_front_without_duplicating_and_cap(tmp_path):
    ws = build_ws(tmp_path)
    root = str(ws.carts_root)
    for i in range(ws.carts._MRU_CAP + 3):
        _mk(root, "Filler %d" % i)
    ws.carts.apply(moy_carts.scan(root))
    made = [c for c in ws.carts.all if c["title"].startswith("Filler ")]

    first = made[0]
    for c in made:
        ws.carts.note_recent(c)
    ws.carts.note_recent(first)
    mru = ws.system["desk_mru"]
    assert mru[0] == first["path"]
    assert len(mru) == ws.carts._MRU_CAP
    assert len(set(mru)) == len(mru)


def test_recent_resolves_paths_back_to_live_carts_and_drops_the_missing(tmp_path):
    ws = build_ws(tmp_path)
    a, b = [c for c in ws.carts.all if c.get("path")][:2]
    ws.carts.note_recent(a)
    ws.carts.note_recent(b)
    assert [c["path"] for c in ws.carts.recent()] == [b["path"], a["path"]]

    ws.system["desk_mru"] = ["gone/forever"] + ws.system["desk_mru"]
    assert [c["path"] for c in ws.carts.recent()] == [b["path"], a["path"]]


def test_a_launcher_run_records_the_cart_as_recent(tmp_path):
    """`open()` is the one caller, and it is the reason `note_recent` could not
    simply be dropped for lack of a rendering surface."""
    ws = build_ws(tmp_path)
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path"))
    cart = ws.launcher.selected()
    ws.launch_selected()
    assert ws.system["desk_mru"][0] == cart["path"]


# -- the frame loop -----------------------------------------------------------

def _workstation_method(name):
    tree = ast.parse(ROOT.joinpath("runtime/console.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Workstation")
    return next(f for f in cls.body
                if isinstance(f, ast.FunctionDef) and f.name == name)


def test_the_frame_loop_never_reaches_the_roster(tmp_path):
    """The roster is cold BY CONSTRUCTION: the only per-frame reader of the cart
    list is the idle cover prefetch, which is CoverCache's own walk. A
    `self.carts` appearing in `frame()` means something started scanning the
    library on the paint path."""
    fn = _workstation_method("frame")
    hits = [n for n in ast.walk(fn)
            if isinstance(n, ast.Attribute) and n.attr == "carts"
            and isinstance(n.value, ast.Name) and n.value.id == "self"]
    assert hits == []


def test_the_cover_prefetch_walks_the_collaborators_list(tmp_path):
    """One author: the idle warmer reads `ws.carts.all` rather than a second
    copy the kernel kept for it."""
    ws = build_ws(tmp_path)
    ws.carts.all = []
    ws.covers._seen = True
    ws.covers._pf_i = 0
    ws.covers.prefetch_tick()               # an empty roster: nothing to warm
    assert ws.covers._pf_i == 0

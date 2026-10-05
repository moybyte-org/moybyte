"""The shelf's catalogue: `moy_carts.catalogue` / `entry` (#224).

The shelf holds every cart as its catalogue entry and reads a cart whole only
to open it. The scan used to load every cart whole and slim it afterwards,
which on a board decoded ~1.5 MB to keep ~50 KB and set the gc heap's size for
the session. What these pin:

  * **the catalogue IS the slimmed whole scan**: for every seed cart, every
    fixture and the sheet shapes below, the shelf a boot builds from the
    catalogue equals, key for key, the shelf `CartManager.slim` makes of the
    same carts loaded whole -- and so do the icons it bakes;
  * **an entry opens no payload file**, and reads only the top of the sprite
    sheet, as far as the icon needs;
  * an entry refuses a folder on `load`'s terms, except that of the scripts
    only the main one's existence is checked.
"""

import json
import shutil
from pathlib import Path

from runtime import host_app, moy_carts, moy_store_base
from runtime.editors_sheet import SpriteSheet

ROOT = Path(__file__).resolve().parent.parent

_ROW = "0" * 128
_INKED = "7" * 128


def _sheet(lines):
    return "\n".join(lines) + "\n"


def _with(root, title, sprites=None, icon=None, **files):
    """A cart under root with a sprite sheet, a manifest icon and any other
    files (name -> text)."""
    cart = moy_carts.create(title, root, src="def _draw():\n    cls(1)\n")
    d = Path(cart["path"])
    if icon is not None:
        man = json.loads((d / "manifest.json").read_text())
        man["icon"] = icon
        (d / "manifest.json").write_text(json.dumps(man))
    if sprites is not None:
        (d / moy_store_base.SPRITES_NAME).write_text(sprites)
    for name, text in files.items():
        p = d / name.replace("__", "/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return d


def _store(tmp_path):
    """Every seed cart, every fixture cart, the tracked port carts, and the
    sheet and folder shapes a scan meets on a card."""
    root = tmp_path / "carts"
    root.mkdir()
    for src in (sorted(ROOT.glob("system_carts/*.moy"))
                + sorted(ROOT.glob("tests/fixtures/**/*.moy"))
                + sorted(ROOT.glob("ports/*/*.moy"))):
        if src.is_dir() and not (root / src.name).exists():
            shutil.copytree(src, root / src.name)
    r = str(root)
    _with(r, "Blank Sheet", _sheet([_ROW] * 256))
    _with(r, "Late Ink", _sheet([_ROW] * 200 + [_INKED] + [_ROW] * 55))
    _with(r, "Span Icon", _sheet(["%0128x" % i for i in range(256)]),
          icon=[200, 2, 2])
    _with(r, "Off Sheet", _sheet([_INKED] * 16), icon=600)
    _with(r, "Edge Span", _sheet([_INKED] * 256), icon=[511, 4, 4])
    _with(r, "Short Sheet", "1234\n\n  5678  \n")
    _with(r, "Crlf Sheet", "\r\n".join([_INKED[:64]] * 12) + "\r\n")
    _with(r, "No Sheet")
    _with(r, "Assets", _sheet([_INKED] * 8),
          **{"sounds.json": json.dumps({"sfx": []}),
             "map.moymap": "00" * 64,
             "flags.moyflags": "01" * 512,
             "blocks.json": json.dumps({"blocks": []}),
             "images__wall.moyimg": "moyimg 1 1 AA==",
             "scenes__b.moyscene": "[]",
             "scenes__a.moyscene": "[]"})
    port = _with(r, "Ported", _sheet([_INKED] * 8),
                 **{"p8.lua": "-- shim\n"})
    man = json.loads((port / "manifest.json").read_text())
    man["sources"] = ["p8.lua", "main.py"]
    (port / "manifest.json").write_text(json.dumps(man))
    broken = _with(r, "Broken Manifest", _sheet([_INKED] * 8))
    (broken / "manifest.json").write_text("{ not json")
    headless = _with(r, "Headless")
    (headless / "main.py").unlink()
    (root / "archive.moy").write_text("a cart in transit, not a folder")
    return r


def _pixels(img):
    return None if img is None else (img.w, img.h, list(img.pix), img.transparent)


def test_the_catalogue_is_the_slimmed_whole_scan(tmp_path):
    """The boot's shelf (catalogue, then slim) against the shelf `slim` makes
    of the same store loaded whole: the same carts, the same keys and values,
    and the same baked icons."""
    root = _store(tmp_path)
    ws = host_app.build_workstation(root)
    shelf = [dict(c) for c in ws.carts.all]
    icons = {k: _pixels(v) for k, v in ws.covers.icons.items()}

    ws.carts.apply(moy_carts.scan(root))
    whole = ws.carts.all
    whole_icons = {k: _pixels(v) for k, v in ws.covers.icons.items()}

    assert len(shelf) > 40
    assert [c["path"] for c in shelf] == [c["path"] for c in whole]
    for mine, theirs in zip(shelf, whole):
        assert mine == theirs, mine["path"]
        assert "icon_rows" not in mine and mine["lazy"] is True
    assert icons == whole_icons
    assert any(v is not None for v in icons.values())
    assert any(v is None for v in icons.values())


def test_an_entry_is_the_whole_cart_without_its_payloads(tmp_path):
    """Store level, every cart: `entry` == `load` minus PAYLOADS, plus the
    icon rows, which bake the same icon the whole sheet does."""
    root = _store(tmp_path)
    entries = moy_carts.catalogue(root)
    wholes = {c["path"]: c for c in moy_carts.scan(root)}
    assert [c["path"] for c in entries] == sorted(wholes)
    for e in entries:
        w = wholes[e["path"]]
        rows = e.pop("icon_rows")
        assert e == {k: v for k, v in w.items() if k not in moy_carts.PAYLOADS}
        n, tw, th = w["icon"] or (0, 1, 1)
        assert _pixels(SpriteSheet.icon_from_rows(rows)) == _pixels(
            SpriteSheet.icon_from_hex(w["sprites"], n, tw, th, cols=16, rows=32))


def test_an_entry_opens_no_payload_file(tmp_path, monkeypatch):
    root = _store(tmp_path)
    read = []
    real = moy_carts._read
    monkeypatch.setattr(moy_carts, "_read",
                        lambda p: (read.append(p), real(p))[1])
    assert moy_carts.catalogue(root)
    names = {p.rsplit("/", 1)[-1] for p in read}
    assert names <= {"manifest.json", "config.json", "flags.moyflags"}, names


def test_the_icon_read_stops_once_it_has_the_art_and_the_rows():
    """The sheet is read as far as the icon needs: a line past that is never
    pulled, so a card's scan reads the top of each sheet and no more."""
    def lines(n_ok):
        for _ in range(n_ok):
            yield _INKED
        raise AssertionError("read past the icon")

    assert moy_store_base.icon_rows(lines(8), 0, 1, 1)[1] == 8
    assert moy_store_base.icon_rows(lines(40), 48, 2, 2)[1] == 16    # pixel rows 24-39
    assert moy_store_base.icon_rows(iter([_ROW] * 256), 0, 1, 1) is None


def test_an_entry_checks_only_the_main_scripts_existence(tmp_path):
    """A listed script that is missing takes a cart off `load`, which runs
    it, but not off the shelf, which does not; a missing main takes it off
    both. A folder named .moy that is a file is no cart either way."""
    root = str(tmp_path / "carts")
    (tmp_path / "carts").mkdir()
    gone = _with(root, "Missing Piece")
    man = json.loads((gone / "manifest.json").read_text())
    man["sources"] = ["p8.lua", "main.py"]
    (gone / "manifest.json").write_text(json.dumps(man))
    headless = _with(root, "Headless")
    (headless / "main.py").unlink()
    (tmp_path / "carts" / "packed.moy").write_text("archive")
    assert [c["title"] for c in moy_carts.catalogue(root)] == ["Missing Piece"]
    assert moy_carts.scan(root) == []

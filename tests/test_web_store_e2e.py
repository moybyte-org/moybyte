"""Get Carts in the HOSTED console, in real Chrome (#124).

Two local servers stand in for the two kinds of host a carts index names:

  * PAGES -- the carts repository's Pages site, which answers every file with
    `Access-Control-Allow-Origin: *`: index.json, the licence texts, and the
    MIRROR of each release asset (moy-spec's `moy index --mirror`);
  * RELEASES -- a GitHub release download and Debian's archive, neither of
    which sends a CORS header, so a page may ask and may not read.

The console is the built dist/ served by serve.py from a static directory with
an `indexes.json` beside it naming the PAGES index (the page's own twin of a
board's indexes.json). The page is on a third origin, so every rule here is
Chrome's own CORS, not a fake's.

What it proves, in order: a cart installs from the mirror and never from the
release; it lands in OPFS whole, binary file and record included, with nothing
left in staging; a cart whose external file's host the page cannot read asks
for the player's own copy, takes it, and installs it; a cart whose bytes are
wrong installs nothing; and on a second load in the same browser the carts and
the record come back out of OPFS and the installed cart PLAYS.

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_store_e2e.py

Gated like its siblings (`tests/web_e2e.py`): a missing toolchain SKIPS on a
bench and FAILS under CI. ~3 Chrome boots, ~90s.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

import pytest

import web_e2e
from cart_store_fixtures import sha, stored_zip, tar_gz
from web_e2e import RUNNER, ROOT, DIST

pytestmark = pytest.mark.skipif(
    not os.environ.get("MOYBYTE_WEB_E2E"),
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome for ~90s)")


def _lua(folder, title, colour, extra=None):
    files = {"manifest.json": json.dumps({"format": "moy-1", "title": title,
                                          "runtime": "lua",
                                          "main": "main.lua"}).encode(),
             "main.lua": ("function _draw()\n  cls(%d)\n  print(%r, 8, 8, 7)\nend\n"
                          % (colour, title)).encode(),
             "LICENSES.txt": b"MIT, by the test.\n"}
    files.update(extra or {})
    return folder, files


def _shelf(pages, releases):
    """Three Lua carts: one plain (with a binary file), one needing an external
    file from a host the page may not read, one whose mirror is corrupt."""
    lic = b"The MIT licence of the test carts.\n"
    pages.files["/carts/LICENCE.txt"] = lic
    carts = []
    data_bin = bytes((i * 37 + (i >> 8)) & 255 for i in range(70000))
    wad = bytes((i * 11 + 3) & 255 for i in range(90000))
    for cid, name, colour, extra, ext, corrupt in (
            ("rock", "Rock Run", 12, {"data.bin": data_bin}, None, False),
            ("dm", "Dungeon", 2, None, ("game.wad", wad), False),
            ("zap", "Zap Bad", 8, None, None, True)):
        folder, files = _lua(cid + ".moy", name, colour, extra)
        z = stored_zip(folder, files)
        tag = "%s-v1" % cid
        rel = "releases/%s/%s.zip" % (tag, folder)
        releases.files["/o/carts/releases/download/%s/%s.zip" % (tag, folder)] = z
        pages.files["/" + rel] = (z[:-40] + b"\0" * 40) if corrupt else z
        entry = {
            "id": cid, "name": name, "version": 1, "folder": folder, "runtime": "lua",
            "licence": {"spdx": "MIT", "name": "MIT License", "url": "carts/LICENCE.txt",
                        "size": len(lic), "sha256": sha(lic)},
            "assets": [{"name": folder + ".zip",
                        "url": "%s/o/carts/releases/download/%s/%s.zip"
                               % (releases.base, tag, folder),
                        "mirror": rel, "size": len(z), "sha256": sha(z),
                        "files": dict((fn, {"size": len(b), "sha256": sha(b)})
                                      for fn, b in files.items())}],
            "external": [],
        }
        if ext:
            path, body = ext
            arc = tar_gz({"pkg/" + path: body})
            releases.files["/debian/pool/%s.tar.gz" % path] = arc
            ltext = b"You may give this file away, and not sell it.\n"
            pages.files["/carts/%s.txt" % path] = ltext
            entry["external"].append({
                "path": path, "size": len(body), "sha256": sha(body),
                "licence": {"name": "The game data's licence", "url": "carts/%s.txt" % path,
                            "size": len(ltext), "sha256": sha(ltext)},
                "archive": {"urls": ["%s/debian/pool/%s.tar.gz" % (releases.base, path)],
                            "format": "tar.gz", "size": len(arc), "sha256": sha(arc),
                            "member": "pkg/" + path}})
        carts.append(entry)
    pages.files["/index.json"] = json.dumps(
        {"version": 1, "name": "Test shelf", "home": releases.base + "/o/carts",
         "carts": carts}).encode()
    return data_bin, wad


def _console(tmp_path, index_url):
    """dist/ as a static site, with the shelves this host chooses beside it."""
    site = tmp_path / "site"
    site.mkdir()
    for name in os.listdir(DIST):
        os.symlink(DIST / name, site / name)
    (site / "indexes.json").write_text(json.dumps({"indexes": [index_url]}))
    return site


def _layout():
    """Where the app draws on the 320x240 handheld tier -- its own layout
    class, so a tap lands where the console drew."""
    sys.path.insert(0, str(ROOT))
    from runtime.getcarts_app import GetCartsLayout
    lay = GetCartsLayout(320, 240, 1, False, 1)

    def centre(r):
        return [r[0] + r[2] // 2, r[1] + r[3] // 2]
    return {"row": [centre(lay.row_rect(i)) for i in range(3)],
            "one": centre(lay.buttons(1)[0]),
            "two": [centre(r) for r in lay.buttons(2)]}


# What the browser's store of record holds, read straight out of OPFS.
_OPFS = """(async () => {
  const root = await navigator.storage.getDirectory();
  const out = {carts: {}, record: null, staging: []};
  const carts = await root.getDirectoryHandle('carts');
  for (const id of %s) {
    try {
      const d = await carts.getDirectoryHandle(id);
      const f = {};
      for await (const [n, h] of d.entries()) {
        const b = new Uint8Array(await (await h.getFile()).arrayBuffer());
        const s = new Uint8Array(await crypto.subtle.digest('SHA-256', b));
        f[n] = [b.length, Array.from(s).map(x => x.toString(16).padStart(2, '0')).join('')];
      }
      out.carts[id] = f;
    } catch (e) { out.carts[id] = null; }
  }
  try { out.record = JSON.parse(await (await (await root.getFileHandle('installed.json')).getFile()).text()); } catch (e) {}
  try { for await (const [n] of (await root.getDirectoryHandle('install')).entries()) out.staging.push(n); } catch (e) {}
  return JSON.stringify(out);
})()"""

_FOLDERS = '["rock.moy", "dm.moy", "zap.moy"]'

_FORGET_APP = """(async () => {
  const carts = await (await navigator.storage.getDirectory()).getDirectoryHandle('carts');
  await carts.removeEntry('get_carts.moy', {recursive: true});
  return 'gone';
})()"""


def test_a_hosted_console_installs_carts_into_its_own_store(tmp_path):
    web_e2e.require("store", "carts")
    pages, releases = web_e2e.Host(cors=True), web_e2e.Host(cors=False)
    data_bin, wad = _shelf(pages, releases)
    (tmp_path / "doom1.wad").write_bytes(wad)
    site = _console(tmp_path, pages.base + "/index.json")
    server, base = web_e2e.serve(site, web_e2e.free_port())
    profile = tmp_path / "chrome"
    at = _layout()
    try:
        out, js = web_e2e.run(tmp_path, "install", "?handheld=1&dev=1&cart=get_carts.moy", [
            {"note": "Get Carts opened at boot; the index came from PAGES", "wait": 4000},
            {"shot": "list"},
            {"js": "window.__moyPersist ? window.__moyPersist.mode : 'none'"},
            {"note": "Rock Run: the plain cart", "click": at["row"][1]},
            {"wait": 800}, {"shot": "rock"},
            {"click": at["one"]},
            {"wait": 5000}, {"shot": "rock_ready"},
            {"js": "window.__moyInstalled || ''"},
            {"js": _OPFS % _FOLDERS},
            {"note": "OK, back to the list, then Dungeon", "click": at["two"][1]},
            {"wait": 800}, {"click": at["row"][0]},
            {"wait": 800}, {"click": at["one"]},
            {"note": "its licence first", "wait": 3000}, {"shot": "licence"},
            {"click": at["two"][0]},
            {"note": "the archive's host cannot be read: the page asks", "wait": 4000},
            {"shot": "your_copy"},
            {"js": "JSON.stringify(window.__moyPick || null)"},
            {"js": "getComputedStyle(document.getElementById('pk')).display"},
            {"file": "$MOY_WAD", "as": "__wad"},
            {"js": "pkSend(new File([window.__wad], 'doom1.wad')), 'sent'"},
            {"wait": 6000}, {"shot": "dm_ready"},
            {"js": "getComputedStyle(document.getElementById('pk')).display"},
            {"js": _OPFS % _FOLDERS},
            {"note": "Zap: its mirror's bytes are wrong", "click": at["two"][1]},
            {"wait": 800}, {"click": at["row"][2]},
            {"wait": 800}, {"click": at["one"]},
            {"wait": 5000}, {"shot": "zap_failed"},
            {"js": _OPFS % _FOLDERS},
            {"note": "a store from before Get Carts shipped: it is not in OPFS",
             "js": _FORGET_APP},
        ], base, profile, env={"MOY_WAD": str(tmp_path / "doom1.wad")})
        assert len(js) == 10, "the scenario did not run to its end:\n%s" % out[-4000:]
        assert js[9] == "gone", js[9]
        mode, installed, after_rock, pick, card, sent, card_after, after_dm, after_zap = \
            js[0], js[1], json.loads(js[2]), json.loads(js[3]), js[4], js[5], js[6], \
            json.loads(js[7]), json.loads(js[8])
        assert mode == "site", "a static host must keep its carts in the browser"
        assert installed == "rock.moy", out[-3000:]

        rock = after_rock["carts"]["rock.moy"]
        assert rock and rock["data.bin"] == [len(data_bin), hashlib.sha256(data_bin)
                                             .hexdigest()], rock
        assert set(rock) == {"manifest.json", "main.lua", "LICENSES.txt", "data.bin"}
        assert after_rock["record"]["carts"]["rock.moy"]["id"] == "rock"
        assert after_rock["staging"] == []
        assert not [p for p in releases.asked if p.endswith(".zip")], \
            "the page asked the release, which it cannot read: %r" % releases.asked

        assert pick == {"id": pick["id"], "name": "game.wad", "size": len(wad)}, pick
        assert card == "block" and sent == "sent" and card_after == "none"
        dm = after_dm["carts"]["dm.moy"]
        assert dm and dm["game.wad"] == [len(wad), hashlib.sha256(wad).hexdigest()], dm
        assert sorted(after_dm["record"]["carts"]) == ["dm.moy", "rock.moy"]
        assert releases.asked.count("/debian/pool/game.wad.tar.gz") >= 1

        assert after_zap["carts"]["zap.moy"] is None, "a wrong cart reached the store"
        assert sorted(after_zap["record"]["carts"]) == ["dm.moy", "rock.moy"]
        assert after_zap["staging"] == []

        # A SECOND LOAD in the same browser: the shelf, the record and the bytes
        # come back out of OPFS -- and the installed cart plays.
        out2, js2 = web_e2e.run(tmp_path, "reload", "?handheld=1&dev=1", [
            {"wait": 2500},
            {"js": "pzAsk(), 'asked'"}, {"wait": 600},
            {"js": "Array.from(document.getElementById('pzc').options)"
                   ".map(o=>o.value).join(',')"},
            {"js": "window.__moyPersist ? window.__moyPersist.d : 'none'"},
        ], base, profile)
        shelf = js2[1]
        assert "rock.moy" in shelf and "dm.moy" in shelf and "zap.moy" not in shelf, \
            "the installs did not survive a reload:\n%s" % out2[-3000:]
        assert "get_carts.moy" in shelf, \
            "a system cart the store never had did not come from the bundle"
        assert js2[2].startswith("loaded "), js2[2]

        out3, js3 = web_e2e.run(tmp_path, "play", "?handheld=1&dev=1&cart=rock.moy", [
            {"wait": 3000}, {"js": "assCart"}, {"shot": "rock_plays"},
            {"js": "JSON.stringify(window.__moyPersist || null)"},
        ], base, profile)
        assert js3[0] == "Rock Run", "the installed cart did not run:\n%s" % out3[-3000:]
        print("\nstore e2e: %s" % js2[2])
    finally:
        server.terminate()
        server.wait(timeout=10)
        pages.stop()
        releases.stop()


@pytest.mark.parametrize("board,home", [("glass", "board"), ("headless", "headless")])
def test_a_board_served_page_gets_no_carts_of_its_own(tmp_path, board, home):
    """BOARD mode: the carts are the serving console's, so the console in the
    page has no network for Get Carts, no keeper, and says where carts come
    from -- by whether that console has a screen -- and fetches nothing, not
    even an index its host names."""
    web_e2e.require("store", "carts")
    pages = web_e2e.Host(cors=True)
    pages.files["/index.json"] = b'{"version": 1, "carts": []}'
    store = tmp_path / "store"
    store.mkdir()
    shutil.copytree(ROOT / "system_carts" / "get_carts.moy", store / "get_carts.moy")
    site = _console(tmp_path, pages.base + "/index.json")
    port = web_e2e.free_port()
    p = subprocess.Popen([sys.executable, "serve.py", str(port), str(site),
                          "--carts", str(store), "--update", board],
                         cwd=RUNNER, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/index.html", timeout=1).read(64)
                break
            except OSError:
                time.sleep(0.1)
        out, js = web_e2e.run(tmp_path, "board", "?handheld=1&dev=1&cart=get_carts.moy", [
            {"wait": 2500},
            {"js": "window.__moyPersist ? window.__moyPersist.mode : 'none'"},
            {"js": "JSON.stringify((window.__moyUpdate || {}).services || null)"},
            {"shot": "on_the_console"},
        ], base, tmp_path / "chrome")
        assert js[0] == "board", out[-2000:]
        svc = json.loads(js[1])
        assert svc and svc["cart_home"] == home, svc
        assert svc["cart_net"] is False and svc["cart_keep"] is False, svc
        assert pages.asked == [], "a board-served page fetched a carts index"
    finally:
        p.terminate()
        p.wait(timeout=10)
        pages.stop()

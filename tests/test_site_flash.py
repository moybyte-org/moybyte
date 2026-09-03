"""The website's board flasher (site/build.py + site/flash.js).

The page writes firmware to a real board, so the interesting failures are not
"the HTML looks wrong" -- they are an image published at the wrong offset, or
against the wrong chip, or silently stale. Three things are pinned here:

  * the assembly: what lands under _site/, and that the manifest the browser
    reads describes the bytes that were actually copied (size + sha256);
  * the gap: no CI image is an ORDINARY state (the firmware workflow is manual
    and artifacts expire), so the page must still build and say so;
  * the parity: site/build.py's BOARDS table is the browser's copy of the
    Makefile's `write_flash` arguments, and the two drifting apart is exactly
    the bug that ends with a board that will not boot.
"""

import hashlib
import importlib.util
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_build():
    # NOT a plain `import site.build`: `site` is a stdlib module, and the
    # directory is not a package. Load it by path under a private name.
    path = os.path.join(ROOT, "site", "build.py")
    spec = importlib.util.spec_from_file_location("_moy_site_build", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


build = _load_build()


def _fake_firmware(root, boards=None, stamp=True):
    """A stand-in for tools/fetch_ci_firmware.py's output tree. Fakes every
    board in the table by default, so board N+1 joins these checks by existing."""
    if boards is None:
        boards = tuple(b["id"] for b in build.BOARDS)
    src = os.path.join(root, "ci-firmware")
    for board in build.BOARDS:
        if board["id"] not in boards:
            continue
        folder = os.path.join(src, board["id"])
        os.makedirs(folder)
        # Its published image, plus a decoy the picker must not choose.
        with open(os.path.join(folder, board["images"][0]), "wb") as f:
            f.write(bytes(range(256)) * 8 + board["id"].encode())
        with open(os.path.join(folder, "not-the-flashable-one.bin"), "wb") as f:
            f.write(b"\xff" * 64)
        if stamp:
            with open(os.path.join(folder, "source.json"), "w") as f:
                json.dump({"commit": "0" * 40, "run_number": 7, "built":
                           "2026-07-29T19:39:21Z", "run_url": "https://x/run"}, f)
    return src


@pytest.fixture
def site(tmp_path):
    """Build the site into tmp with fake CI firmware; return (out, html)."""
    src = _fake_firmware(str(tmp_path))
    out = str(tmp_path / "_site")
    sys.argv = ["build.py", "--out", out, "--no-player", "--firmware", src]
    build.main()
    return out, open(os.path.join(out, "index.html"), encoding="utf-8").read()


def test_publishes_one_image_per_board(site):
    out, _ = site
    manifest = json.load(open(os.path.join(out, "firmware", "manifest.json")))
    assert [b["id"] for b in manifest["boards"]] == [b["id"] for b in build.BOARDS]
    for entry in manifest["boards"]:
        blob = open(os.path.join(out, entry["url"]), "rb").read()
        # The browser refuses to flash an image that misses either of these, so
        # a manifest that disagrees with the file is a broken page, not a nit.
        assert entry["size"] == len(blob)
        assert entry["sha256"] == hashlib.sha256(blob).hexdigest()
        assert entry["file"] not in ("not-the-flashable-one.bin",)


def test_manifest_carries_what_the_flasher_needs(site):
    _, html = site
    blob = re.search(r'<script type="application/json" id="fw-manifest">(.*?)</script>',
                     html, re.S)
    assert blob, "the page must embed the manifest it flashes from"
    boards = json.loads(blob.group(1))["boards"]
    for entry, table in zip(boards, build.BOARDS):
        assert entry["offset"] == table["offset"]
        assert entry["chip"] == table["chip"]        # the wrong-board guard
        assert entry["reset"] == table["reset"]
        assert entry["baud"] == table["baud"]
        assert entry["usb_otg"] == table["usb_otg"]
        assert entry["after"] == table["after"]
        assert entry["done"] == table["done"]
    assert '<script type="module" src="flash.js"></script>' in html


def test_a_board_that_cannot_be_reset_says_so_rather_than_trying():
    """The T-Deck's reset line is unreachable over its own USB at BOTH ends, so
    the page must not claim the board is running when the write finishes."""
    tdeck = {b["id"]: b for b in build.BOARDS}["tdeck"]
    assert tdeck["reset"] == "no_reset"      # in: the human holds the trackball
    assert tdeck["after"] is None            # out: the human presses RST
    assert "RST" in tdeck["done"] and "RST" in tdeck["prep"]
    js = open(os.path.join(ROOT, "site", "flash.js"), encoding="utf-8").read()
    # `after` is asked for only where it exists, and the finish line is the
    # board's own -- not a blanket "the board is running this build".
    assert "b.after &&" in js                      # the call is guarded
    assert "loader.after(b.after" in js
    assert "b.done" in js
    assert "board is running this build" not in js


def test_a_reset_that_will_not_take_has_a_way_out():
    """Auto-reset is the part most likely to fail on someone else's machine, so
    every board that attempts one must offer to skip it."""
    for board in build.BOARDS:
        if board["reset"] == "no_reset":
            assert board["manual"] is None, "nothing to skip on %s" % board["id"]
        else:
            assert board["manual"], "%s can strand a user" % board["id"]
            assert "BOOT" in board["manual"]
    js = open(os.path.join(ROOT, "site", "flash.js"), encoding="utf-8").read()
    assert '.manual input' in js                   # the card's checkbox
    assert 'manual ? "no_reset" : b.reset' in js
    # Failures must say what to do next, not just what went wrong.
    assert "/connect/i.test(msg)" in js


def test_the_lost_device_trap_is_explained():
    """Confirmed on a P4: a pyserial tool leaves VMIN=0 on the tty, that
    outlives it, and Chrome then calls the still-enumerated port disconnected.
    Nobody guesses that from "The device has been lost", so the page says it."""
    js = open(os.path.join(ROOT, "site", "flash.js"), encoding="utf-8").read()
    assert "/device has been lost/i.test(msg)" in js
    assert "unplug" in js
    readme = open(os.path.join(
        ROOT, "firmware", "esp32_p4_wifi6_touch_lcd_7b", "README.md"),
        encoding="utf-8").read()
    assert "The device has been lost" in readme and "min 1" in readme


def test_ships_the_flasher_itself(site):
    out, _ = site
    # Vendored, never a CDN: the page must be able to flash offline, and what
    # writes to hardware is a pinned file in the repo (THIRD_PARTY.md 2.4).
    assert os.path.exists(os.path.join(out, "vendor", "esptool-js", "bundle.js"))
    assert os.path.exists(os.path.join(out, "vendor", "esptool-js", "LICENSE"))
    assert os.path.exists(os.path.join(out, "flash.js"))
    js = open(os.path.join(out, "flash.js"), encoding="utf-8").read()
    assert "./vendor/esptool-js/bundle.js" in js
    assert "http://" not in js and "https://" not in js


def test_a_board_with_no_ci_build_is_not_an_error(tmp_path):
    """Artifacts expire and the build is manual: the page says so and stands."""
    src = _fake_firmware(str(tmp_path), boards=("p4",))
    out = str(tmp_path / "_site")
    sys.argv = ["build.py", "--out", out, "--no-player", "--firmware", src]
    build.main()
    html = open(os.path.join(out, "index.html"), encoding="utf-8").read()
    manifest = json.load(open(os.path.join(out, "firmware", "manifest.json")))
    assert [b["id"] for b in manifest["boards"]] == ["p4"]
    assert "no published build" in html
    # ... and offers the way to make one, rather than a dead card.
    assert "make firmware-flash-lilygo-micropython-full" in html
    assert not os.path.isdir(os.path.join(out, "firmware", "tdeck"))


def test_no_firmware_at_all_still_builds_a_page(tmp_path):
    out = str(tmp_path / "_site")
    sys.argv = ["build.py", "--out", out, "--no-player",
                "--firmware", str(tmp_path / "nothing-here")]
    build.main()
    html = open(os.path.join(out, "index.html"), encoding="utf-8").read()
    assert html.count("no published build") == len(build.BOARDS)
    # Nothing to flash -> none of the flasher's weight is shipped.
    assert "flash.js" not in html
    assert not os.path.exists(os.path.join(out, "flash.js"))
    assert not os.path.isdir(os.path.join(out, "vendor"))


def _declared_flash():
    """{`[board] ota` id: (its dir, its [flash] block)} for every board that
    declares a cable flash. DERIVED from the tree rather than listed, so board
    N+1 joins the checks below by existing -- the hand-written map this replaced
    named two of the four boards, and the two it left out were never compared
    against their own declaration at all."""
    from tools import board_config
    import glob
    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "firmware", "*", "board.toml"))):
        d = os.path.dirname(path)
        cfg = board_config.load(d)
        ota = (cfg.get("board") or {}).get("ota")
        if ota and "flash" in cfg:
            out[ota] = (d, cfg["flash"])
    return out


def test_the_page_writes_what_the_cable_flash_writes():
    """The Makefile writes these images over a cable; the site writes the same
    ones over USB from a browser. If the two disagree on the offset, one of them
    bricks a boot; if they disagree on the FILE, the page hands a board an image
    built for something else. So read each board's own declaration and compare.

    This also closes the id loop: the site's board id has to BE the board.toml
    `[board] ota` id, which is the same string as the CI matrix row (below), the
    OTA_IMAGES key and the `latest-<board>.json` a device asks for. Four places,
    one string, and a mismatch is silent everywhere."""
    # The cable flash reads its facts from each board.toml [flash] section
    # (#202 Phase A) -- so compare the site's table against the DECLARATION,
    # which is stronger than the old Makefile regex ever was.
    declared = _declared_flash()
    assert len(declared) >= 4, "board discovery found %d" % len(declared)
    for board in build.BOARDS:
        bid = board["id"]
        assert bid in declared, (
            "the site offers %s, which no firmware/*/board.toml declares as its "
            "`[board] ota` id" % bid)
        _, fl = declared[bid]
        assert int(str(fl["offset"]), 16) == board["offset"], bid
        assert int(fl["baud"]) == board["baud"], bid
        # ...and the image the site publishes is the one the cable flash writes.
        assert str(fl["image"]).split("/")[-1] == board["images"][0], bid


def test_a_tinyusb_cdc_board_is_never_reset_by_the_page():
    """The Zero (#41) shares the Guition's chip and NOT its reset fields, which
    is the one thing on that card that cannot be inherited by analogy.

    It keeps MicroPython's TinyUSB CDC (303a:4001) rather than the console
    boards' USB-Serial/JTAG promotion, and esptool-js chooses its reset sequence
    off the PID: the JTAG path is taken for 0x1001, so on 0x4001 anything but
    `no_reset` falls through to the classic DTR/RTS dance -- which against this
    board's RUNNING CDC has wedged the USB device, unrecoverable without a
    replug. Coming back out is the mirror image: `hard_reset` is an RTS wiggle
    with no circuit behind it, and the `watchdog_reset` that does work here is
    not implemented by esptool-js at all."""
    zero = {b["id"]: b for b in build.BOARDS}["xiao_zero"]
    assert zero["chip"] == "ESP32-S3"
    assert zero["reset"] == "no_reset"        # in: the human holds BOOT
    assert zero["after"] is None              # out: the human replugs
    assert "BOOT" in zero["prep"] and "plug" in zero["done"]
    # The board's own file is the authority for both halves; if it stops saying
    # so, the values above are the ones to revisit.
    toml = open(os.path.join(ROOT, "firmware", "seeed_xiao_esp32s3_zero",
                             "board.toml"), encoding="utf-8").read()
    assert '303a:4001' in toml
    assert "watchdog_reset" in toml and "hard_reset" in toml
    # It IS the cart store -- no card slot -- so its erase warning cannot borrow
    # the Guition's "with a TF card in the slot the cartridges survive it". Both
    # halves matter to whoever ticks that box: where the carts are, and that
    # nothing here comes through.
    erase = zero["erase"].lower()
    assert "internal flash" in erase
    assert "survive" not in erase


def test_the_fetcher_and_the_workflow_agree_on_artifact_names():
    """The site can only publish what CI uploaded under the name it looks for."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import fetch_ci_firmware as fetch
    finally:
        sys.path.pop(0)
    wf = open(os.path.join(ROOT, ".github", "workflows", "firmware-build.yml"),
              encoding="utf-8").read()
    assert "name: moybyte-firmware-${{ matrix.board }}" in wf
    assert fetch.ARTIFACT % "tdeck" == "moybyte-firmware-tdeck"
    assert set(fetch.BOARDS) == {b["id"] for b in build.BOARDS}
    # Every board of the matrix has a card on the site, and vice versa -- except
    # for the ones named below. The matrix is include-rows since #202 Phase A
    # (one row per board), so read the `- board:` row keys.
    rows = re.findall(r"^\s+- board: (\S+)", wf, re.M)
    # A CARD ON THE SITE IS NOT AUTOMATIC, and a gap is named rather than
    # tolerated. The two directions are not symmetrical: a site card with no
    # matrix row is a page offering an image nothing builds, which is always a
    # bug; a matrix row with no card is a board CI builds and the website cannot
    # flash, which is a missing feature. This set is the second kind, and it is
    # compared EXACTLY -- so an entry has to be deleted the day its card lands
    # (this test says so), and a board that silently loses its card is caught.
    #
    # EMPTY since 2026-08-29, when the Zero got its card: every board CI builds
    # can be flashed from the page. Its entry sat here from the board's
    # promotion that morning until the card landed the same day -- which is what
    # this mechanism is for, and the shape to reuse for board N+1.
    no_site_card = {}
    assert set(fetch.BOARDS) - set(rows) == set(), (
        "the site offers a board CI does not build: %s"
        % sorted(set(fetch.BOARDS) - set(rows)))
    assert set(rows) - set(fetch.BOARDS) == set(no_site_card), (
        "matrix rows with no site card must be named above, with why -- "
        "unexplained: %s"
        % sorted((set(rows) - set(fetch.BOARDS)) - set(no_site_card)))

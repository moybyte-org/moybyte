"""The C6 co-processor updater (#7/#58): the backend behind Settings ->
UPGRADE C6 RADIO on the P4. Driven with a fake OtaUpdater and a fake moy_c6 so
the whole check -> download -> flash -> activate chain runs in plain CI; the
signature-policy pieces are exercised through the real verify path in
test_ota_signing.py."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    p = ROOT / "device" / "moy_c6_update.py"
    spec = importlib.util.spec_from_file_location("moy_c6_update_t", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


C6 = {"version": 2, "hosted": "2.12.12", "url": "http://x/c6.bin",
      "size": 30, "sha256": "ab" * 32}


class _FakeOta:
    """The slice of OtaUpdater the C6 flow rides: manifest fetch + download."""

    def __init__(self, manifest=None, absent=False, from_card=True):
        self.manifest = manifest
        self.absent = absent
        self.from_card = from_card       # card source: no signature required
        self.error = None
        self.dl_done = 0
        self.dl_total = 0
        self.began = None
        self.steps = 0
        self.finish_path = "/moy/update/firmware.bin"

    def check_online(self, channel=None):
        return self.manifest

    def begin_download(self, m):
        self.began = m
        self.dl_total = int(m.get("size") or 0)

    def download_step(self):
        self.steps += 1
        self.dl_done = min(self.dl_total, self.dl_done + 10)
        return self.dl_done < self.dl_total

    def download_finish(self):
        return self.finish_path


class _FakeC6:
    def __init__(self, version=None):
        self._version = version
        self.began = 0
        self.writes = []
        self.ended = 0
        self.activated = 0

    def shim_version(self):
        return self._version

    def ota_begin(self):
        self.began += 1

    def ota_write(self, chunk):
        self.writes.append(bytes(chunk))

    def ota_end(self):
        self.ended += 1

    def ota_activate(self):
        self.activated += 1


def _updater(mod, manifest, shim=None, **kw):
    ota = _FakeOta(manifest, **kw)
    c6 = _FakeC6(shim)
    return mod.C6Updater(ota, c6=c6), ota, c6


def test_a_stock_or_v1_slave_reads_as_older_than_everything():
    """shim_version() answers None for a stock slave AND the v1 shim (both
    predate the verb), and None must always be offered an upgrade -- that IS
    the 'espnow is not available' case the Settings row exists for."""
    mod = _load()
    u, _ota, _c6 = _updater(mod, {"board": "p4", "c6": dict(C6)}, shim=None)
    assert u.check() == "offer"
    assert u.offer == dict(C6)
    assert u.installed is None


def test_a_current_shim_is_up_to_date_and_a_newer_manifest_offers():
    mod = _load()
    u, _o, _c = _updater(mod, {"board": "p4", "c6": dict(C6)}, shim=2)
    assert u.check() == "uptodate"
    u2, _o2, _c2 = _updater(mod, {"board": "p4", "c6": dict(C6, version=3)}, shim=2)
    assert u2.check() == "offer"


def test_a_manifest_without_the_block_is_nothing_to_offer_not_an_error():
    mod = _load()
    u, _o, _c = _updater(mod, {"board": "p4"}, shim=None)
    assert u.check() == "nopublish"
    u2, ota, _c2 = _updater(mod, None, shim=None, absent=True)
    assert u2.check() == "nopublish"


def test_check_failures_surface_the_updaters_error():
    mod = _load()
    u, ota, _c = _updater(mod, None)
    ota.error = "wifi offline"
    assert u.check() == "error"
    assert u.error == "wifi offline"


def test_the_download_is_the_shared_machinery_with_the_c6_block_as_manifest():
    """begin_download must hand OtaUpdater the c6 block itself: url, size and
    sha256 are what the shared streaming verifies against, and those bytes are
    pinned by c6_sig -- one download path, one verify, zero new network code."""
    mod = _load()
    u, ota, _c = _updater(mod, {"board": "p4", "c6": dict(C6)}, shim=None)
    assert u.check() == "offer"
    u.begin_download()
    assert ota.began == dict(C6)
    while u.download_step():
        pass
    assert u.download_finish() == ota.finish_path


def test_the_flash_streams_the_file_in_rpc_sized_chunks(tmp_path):
    """1500-byte chunks: the hosted RPC's own example size -- 4096 fails EIO
    (Phase D). Every byte must land before ota_end, and activate only fires
    when the caller asks (the UI shows DONE and reboots the console)."""
    mod = _load()
    img = tmp_path / "c6.bin"
    img.write_bytes(bytes(range(256)) * 20)          # 5120 B: 3 full + 1 short
    u, _o, c6 = _updater(mod, {"board": "p4", "c6": dict(C6)}, shim=None)
    u.begin_flash(str(img))
    assert c6.began == 1
    while u.flash_step():
        pass
    assert u.finish_flash() is True
    assert c6.ended == 1
    assert [len(w) for w in c6.writes] == [1500, 1500, 1500, 620]
    assert b"".join(c6.writes) == img.read_bytes()
    assert u.fl_done == 5120
    assert c6.activated == 0, "activate is the caller's explicit step"
    assert u.activate() is True
    assert c6.activated == 1


def test_a_write_failure_stops_the_flash_with_the_error_named(tmp_path):
    mod = _load()
    img = tmp_path / "c6.bin"
    img.write_bytes(b"x" * 3000)
    u, _o, c6 = _updater(mod, {"board": "p4", "c6": dict(C6)}, shim=None)

    def boom(chunk):
        raise OSError("EIO")

    c6.ota_write = boom
    u.begin_flash(str(img))
    assert u.flash_step() is False
    assert "EIO" in u.error
    assert u.finish_flash() is False, "a short image must never reach ota_end"


def test_an_unsigned_c6_block_needs_no_signature_from_a_card_source():
    """The #53 doctrine verbatim: a card manifest is physical consent, so the
    LAN dev loop works keyless; the baked-url case is exercised against the
    real verify path in test_ota_signing.py."""
    mod = _load()
    u, _o, _c = _updater(mod, {"board": "p4", "c6": dict(C6)},
                         shim=None, from_card=True)
    assert u.check() == "offer"

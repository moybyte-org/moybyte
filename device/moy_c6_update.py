"""The C6 co-processor updater (#7/#58): download + flash the radio's firmware
FROM the console, over the SDIO link it already shares.

The P4's radio is a second processor with its own firmware -- the esp-hosted
slave plus the moybyte ESP-NOW shim (docs/espnow_p4_2026-08.md). Until
2026-08-25 installing it was a dev-desk operation; this module is the backend
behind Settings -> UPGRADE C6 RADIO, driven a step per frame by UpdateUI
exactly the way OtaUpdater is.

WHAT IT REUSES, deliberately. The manifest is the P4's OWN latest-p4.json --
OtaUpdater.check_online fetched, board-checked and signature-gated it already;
this module reads the `c6` block out of the result. The download IS
OtaUpdater's (begin_download reads url/size/sha256 from any manifest-shaped
dict and streams to the update dir with a running sha), so the C6 image gets
the same streaming, the same progress meters and the same verify-on-finish as
an app image, with zero new network code.

WHAT IS ITS OWN, and why:

  * `c6_sig`. The manifest's `sig` covers only the app fields (widening it
    would make every deployed verifier refuse the manifest), so the c6 block
    carries its own signature over ota_sign.C6_SCHEME's canonical -- without
    it, an attacker who cannot touch the signed app entry could still rewrite
    the c6 block and hand the RADIO their own firmware. Required exactly when
    the manifest-level policy required one (baked url + baked keys; a card
    manifest is physical consent, same doctrine as #53).
  * The version gate is SELF-REPORTED: moy_c6.shim_version() asks the slave
    what it runs (MOYC6_V_VERSION). A stock slave and the v1 shim both answer
    nothing, which reads as "older than everything" -- which is what they are.
    No local bookkeeping file to go stale.
  * The flash is the hosted streamed slave OTA (moy_c6.ota_*), 1500-byte
    chunks -- the RPC's own example size; 4096 fails EIO (Phase D).
  * THE FLOW ENDS IN A FULL CONSOLE REBOOT, never a live re-verify. After
    ota_activate the C6 restarts under a host whose WiFi, webhost and NimBLE
    all hold state against the old slave -- and NimBLE against a vanished
    controller is the exact npl-corruption minefield the D-tail hunt mapped.
    A reboot re-runs the proven boot bring-up instead.

Board-gated by injection: run_desktop constructs one only where moy_c6 exists
(the P4), everything shared stays None-guarded, and the Settings row rides
`ws.c6_updater is not None` like every capability row.
"""

FLASH_CHUNK = 1500     # the hosted slave-OTA example size; 4096 fails EIO


class C6Updater:
    """The step-driven backend UpdateUI pumps: check -> download -> flash ->
    activate, one small piece per painted frame."""

    def __init__(self, updater, c6=None):
        self.updater = updater          # the board's OtaUpdater (manifest+download)
        self._c6 = c6                   # injectable for tests; None -> import moy_c6
        self.error = None
        self.offer = None               # the manifest's c6 block, when newer
        self.installed = None           # shim_version() at last check (None = none/old)
        self.fl_done = 0
        self.fl_total = 0
        self._f = None

    def _mod(self):
        if self._c6 is None:
            import moy_c6
            self._c6 = moy_c6
        return self._c6

    def shim_version(self):
        """What the slave says it runs, or None (stock slave, v1 shim, or a
        transport that is down)."""
        try:
            return self._mod().shim_version()
        except Exception:  # noqa: BLE001 -- no module / transport down
            return None

    # -- check ---------------------------------------------------------------

    def check(self, channel=None):
        """Fetch the board manifest and decide. Returns one of:
        "offer" (self.offer set), "uptodate", "nopublish", "error"
        (self.error set). Blocking network call -- run behind CHECKING...,
        one frame after the screen paints, like check_online itself."""
        self.error = None
        self.offer = None
        u = self.updater
        if u is None:
            self.error = "no updater"
            return "error"
        m = u.check_online(channel)
        if m is None:
            if getattr(u, "absent", False):
                return "nopublish"
            self.error = u.error or "no manifest"
            return "error"
        c6 = m.get("c6")
        if not c6:
            # A manifest without the block: published before the C6 pipeline,
            # or a channel that has not rebuilt since. Nothing to offer is not
            # an error.
            return "nopublish"
        # The block's own signature, under the app manifest's policy: required
        # exactly when the manifest itself had to be signed. A c6_sig that IS
        # present is checked regardless of source, so a tampered official
        # manifest cannot be laundered through a LAN mirror.
        sig = m.get("c6_sig")
        if sig is not None and not self._verify_c6(m, sig):
            self.error = "bad c6 signature"
            return "error"
        if sig is None and self._require_signature():
            self.error = "unsigned c6 image"
            return "error"
        try:
            want = int(c6.get("version") or 0)
        except (TypeError, ValueError):
            self.error = "bad c6 version"
            return "error"
        self.installed = self.shim_version()
        if self.installed is not None and self.installed >= want:
            return "uptodate"
        self.offer = c6
        return "offer"

    def _require_signature(self):
        try:
            import moy_ota
            return bool(moy_ota.OTA_PUBLIC_KEYS) \
                and not getattr(self.updater, "from_card", False)
        except Exception:  # noqa: BLE001 -- no keys baked -> nothing to require
            return False

    def _verify_c6(self, manifest, sig):
        try:
            import moy_ota
            return moy_ota.verify_sig(self._canonical_c6(manifest), sig)
        except Exception:  # noqa: BLE001 -- a verify that cannot run is a refusal
            return False

    @staticmethod
    def _canonical_c6(manifest):
        """MIRRORS tools/ota_sign.canonical_c6 -- change one, change both
        (tests/test_ota_signing.py pins that they agree)."""
        c6 = manifest.get("c6") or {}
        return ("%s\n%s\n%d\n%d\n%s" % (
            "moybyte-c6-v1",
            manifest.get("board") or "",
            int(c6.get("version") or 0),
            int(c6.get("size") or 0),
            (c6.get("sha256") or "").lower(),
        )).encode()

    # -- download (delegated: same streaming, same meters, same verify) ------

    def begin_download(self):
        self.updater.begin_download(self.offer)

    def download_step(self):
        return self.updater.download_step()

    def download_finish(self):
        """The verified path on success (size + sha checked against the SIGNED
        c6 block by the shared machinery), else None with error set."""
        path = self.updater.download_finish()
        if path is None:
            self.error = self.updater.error or "verify failed"
        return path

    def download_cancel(self):
        try:
            self.updater.download_cancel()
        except Exception:  # noqa: BLE001 -- teardown must never raise
            pass

    @property
    def dl_done(self):
        return self.updater.dl_done if self.updater else 0

    @property
    def dl_total(self):
        return self.updater.dl_total if self.updater else 0

    # -- flash (the hosted streamed slave OTA) -------------------------------

    def begin_flash(self, path):
        """Open the verified image and the slave's OTA. Raises on refusal --
        shown to the kid by the UI, same contract as OtaUpdater.begin."""
        self.error = None
        self.fl_done = 0
        import os
        self.fl_total = os.stat(path)[6]
        self._f = open(path, "rb")
        try:
            self._mod().ota_begin()
        except Exception:
            self._close()
            raise

    def flash_step(self):
        """One chunk file -> SDIO. True while more remains; False at EOF or on
        error (error set)."""
        f = self._f
        if f is None:
            return False
        try:
            chunk = f.read(FLASH_CHUNK)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self._close()
            return False
        if not chunk:
            return False
        try:
            self._mod().ota_write(chunk)
        except Exception as exc:  # noqa: BLE001
            self.error = "c6 write: %s" % exc
            self._close()
            return False
        self.fl_done += len(chunk)
        return True

    def finish_flash(self):
        """Close out the stream. True when every byte landed and the slave
        accepted the end-of-image; the caller then calls activate()."""
        self._close()
        if self.fl_total and self.fl_done != self.fl_total:
            self.error = self.error or ("flash %d/%d" % (self.fl_done, self.fl_total))
            return False
        try:
            self._mod().ota_end()
        except Exception as exc:  # noqa: BLE001
            self.error = "c6 ota_end: %s" % exc
            return False
        return True

    def activate(self):
        """Point the C6 at the new image and reboot IT. The caller shows DONE
        and reboots the CONSOLE -- see the module header for why there is no
        live re-verify."""
        try:
            self._mod().ota_activate()
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = "c6 activate: %s" % exc
            return False

    def cancel(self):
        self.download_cancel()
        self._close()

    def _close(self):
        f = self._f
        self._f = None
        if f is not None:
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass

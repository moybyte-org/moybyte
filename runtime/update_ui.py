"""The firmware-update (OTA) screen's UI layer (#53), extracted from Workstation
(runtime/console.py).

The split follows the same discipline as the console's other sub-UIs:
  * The update *queries* + channel config -- Workstation._update_available /
    _online_update_available / _ota_channel / _cycle_channel -- STAY on
    Workstation. Settings calls them to build its rows, tests pin their defs to
    the console module, and _ota_channel is read from several draw paths. They
    read the injected `updater` + `system` config; they are not screen state.
  * UpdateUI (here) -- the update SCREEN itself: open/confirm/download/install/
    done lifecycle, its per-phase input + pointer handling, the per-frame pump
    (which advances the SD/WiFi install one chunk between panel flushes), and
    the drawing. It owns the screen's transient state (_upd_phase / _upd_msg /
    _upd_bin / _online_manifest / _check_armed / _upd_at).

Dependency profile (the facade lens, shell_architecture_v1.md §2) -- through its
`self.ws` back-reference:
  * shared (non-privileged): ws.screen, ws.layout, ws.sys_canvas, ws._dirty,
                             ws._set_text_mode, ws.show_achievements, ws.go_home,
                             ws._glyph, ws._mini_btn, ws._ota_channel
  * privileged (draft make_system_api): ws.updater -- the OtaUpdater handle
                             (find_bin/begin/step/finish/check_online/
                             begin_download/download_step/reset). This is the one
                             genuinely privileged capability the screen needs; it
                             is injected onto Workstation by moy_runtime.run_desktop.

`NAMES` / `in_rect` / `err_text` are injected at construction (same circular-import
reason as BlockEditorUI: console.py builds the one UpdateUI a Workstation holds).
`_ticks_ms` / `_ticks_diff` are duplicated here (they only wrap `time`; the same
foundational-helper duplication BlockEditorUI's layout constants use), so the
method bodies stay byte-for-byte identical to the pre-extraction versions.
"""


try:                                    # device: ticks is frozen flat
    from ticks import _ticks_ms, _ticks_diff
except ImportError:                     # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_diff


class UpdateUI:
    def __init__(self, ws, names, in_rect, err_text):
        self.ws = ws
        # Injected instead of imported back from console.py (see module docstring).
        self._NAMES = names
        self._in = in_rect
        self._err_text = err_text
        # Update-screen transient state (was Workstation's; _updater_ok/_online_ok
        # stay there -- they back the queries, not the screen).
        self._upd_phase = None
        self._upd_msg = ""            # update screen: error / status text
        self._upd_bin = None          # update screen: (path, size) of the found/downloaded image
        self._upd_at = 0              # update screen: timestamp the install finished
        self._online_manifest = None  # the fetched update manifest dict
        self._check_armed = False     # one-frame gate so CHECKING... draws before the blocking fetch

    def open_update(self):
        """Open the firmware-update screen: scan SD for an image to install. Lands on
        the "confirm" phase when one is found, else "error" with a friendly reason."""
        self.ws.wm.goto("update")     # Stage 6e: push the update screen onto the back-stack
        self.ws._dirty = True
        self.ws.show_achievements = False
        self.ws._set_text_mode(False)             # button-driven, not typing
        self._upd_bin = None
        self._upd_msg = ""
        u = self.ws.updater
        if u is None:
            self._upd_phase = "error"
            self._upd_msg = "no updater"
            return
        if self._boot_verdict_phase():
            return
        found = u.find_bin()                    # SD op (between frames)
        if not found:
            self._upd_phase = "error"
            self._upd_msg = "no .bin in /sd/update"
            return
        self._upd_bin = found
        self._upd_phase = "confirm"

    def open_update_online(self):
        """Open the online-update flow (#53 Phase 3): connect WiFi + fetch the manifest,
        and if it's newer, download the image to SD. The blocking check runs in
        _pump_update one frame later so a CHECKING... screen shows first."""
        self.ws.wm.goto("update")     # Stage 6e: push the update screen onto the back-stack
        self.ws._dirty = True
        self.ws.show_achievements = False
        self.ws._set_text_mode(False)
        self._upd_bin = None
        self._upd_msg = ""
        self._online_manifest = None
        if self.ws.updater is None:
            self._upd_phase = "error"
            self._upd_msg = "no updater"
            return
        if self._boot_verdict_phase():
            return
        self._check_armed = False              # gate: draw CHECKING... before the blocking fetch
        self._upd_phase = "checking"

    def _boot_verdict_phase(self):
        """If the last install left a verdict, show THAT before anything else.

        A rollback is otherwise completely silent: the kid sat through a download
        and an install, waited out a reboot, and landed back on the firmware they
        started with, with nothing anywhere saying so. Reading the verdict clears
        it, so it interrupts exactly once and the screen behaves normally after.
        Returns True when it took the screen over."""
        u = self.ws.updater
        verdict = getattr(u, "boot_verdict", None)
        if not verdict:
            return False
        u.boot_verdict = None
        self._upd_phase = "updated" if verdict[0] == "ok" else "rolledback"
        self._upd_msg = verdict[1]
        return True

    def _start_download(self):
        """Open the socket + SD file and switch to the streaming download phase."""
        u = self.ws.updater
        if u is None or not self._online_manifest:
            return
        self.ws._dirty = True
        try:
            u.begin_download(self._online_manifest)
            self._upd_phase = "downloading"
        except Exception as exc:               # noqa: BLE001 -- shown to the kid
            self._upd_phase = "error"
            self._upd_msg = self._err_text(exc)[:30]

    def _exit_update(self):
        """Leave the update screen back to Settings, dropping any in-progress install
        (the inactive slot may be half-written, but it was never set bootable) or
        download (the socket + partial SD file are closed)."""
        u = self.ws.updater
        if u is not None:
            try:
                u.cancel()
            except Exception:
                pass
            try:
                u.download_cancel()
            except Exception:
                pass
        self.ws.wm.goto("settings")   # Stage 6e: pop the update screen, back to Settings
        self.ws._dirty = True

    def _confirm_update(self):
        """Begin flashing the found image (validates header + size, opens the slot)."""
        u = self.ws.updater
        if u is None or not self._upd_bin:
            return
        self.ws._dirty = True
        try:
            u.begin(self._upd_bin[0])
            self._upd_phase = "install"
        except Exception as exc:               # noqa: BLE001 -- shown to the kid
            self._upd_phase = "error"
            self._upd_msg = self._err_text(exc)[:30]

    def _update_input(self, i):
        ph = self._upd_phase
        if i.pressed("home") or i.pressed("stop"):
            if ph != "done":                   # "done" is past the point of no return
                self._exit_update()
                self.ws.go_home()
            return
        if ph == "confirm":
            if i.pressed("a") or i.pressed("run"):
                self._confirm_update()
            elif i.pressed("b"):
                self._exit_update()
        elif ph == "confirm_online":
            if i.pressed("a") or i.pressed("run"):
                self._start_download()
            elif i.pressed("b"):
                self._exit_update()
        elif ph in ("install", "downloading", "checking"):
            if i.pressed("b"):                 # abort: nothing bootable was committed yet
                self._exit_update()
        elif ph in ("error", "uptodate", "updated", "rolledback", "nopublish"):
            if i.pressed("b") or i.pressed("a"):
                self._exit_update()
        # "done": ignore input -- _pump_update reboots into the new image shortly.

    def _update_pointer(self, px, py, click):
        if not click:
            return
        if self._in(px, py, self.ws.layout.set_back):  # the X in the title row
            if self._upd_phase != "done":
                self._exit_update()
            return
        ph = self._upd_phase
        if ph == "confirm":
            self._confirm_update()             # tap anywhere (besides X) = install
        elif ph == "confirm_online":
            self._start_download()             # tap anywhere (besides X) = download
        elif ph in ("error", "uptodate", "updated", "rolledback", "nopublish"):
            self._exit_update()

    def _pump_update(self, dt):
        """Advance the install one chunk (called each painted frame on the update
        screen). Drives begin->step*N->finish->reset through the updater backend."""
        u = self.ws.updater
        ph = self._upd_phase
        if ph == "checking":
            # Run the blocking connect + manifest fetch ONE frame after entry, so the
            # CHECKING... screen paints first (this method runs before _draw each frame).
            if not self._check_armed:
                self._check_armed = True
                return
            if u is None:
                self._upd_phase = "error"
                self._upd_msg = "no updater"
                return
            ch = self.ws._ota_channel()
            manifest = u.check_online(ch)      # connect (saved creds) + GET the manifest
            if u.error:
                self._upd_phase = "error"
                self._upd_msg = u.error
                return
            if getattr(u, "absent", False):
                # Nothing published on this channel for this console yet -- the
                # normal state of a channel before its first release, and not
                # something the kid's machine did wrong.
                self._upd_phase = "nopublish"
                return
            if not manifest:
                self._upd_phase = "error"
                self._upd_msg = "no manifest"
                return
            # Offer when the manifest is a different channel (a switch -- incl. beta->
            # stable) or a newer version within the selected channel (#53).
            if not u.offers(manifest, ch):
                self._upd_phase = "uptodate"
                return
            self._online_manifest = manifest
            self._upd_phase = "confirm_online"
        elif ph == "downloading":
            if u is None:
                self._upd_phase = "error"
                self._upd_msg = "no updater"
                return
            more = u.download_step()           # one chunk: socket -> SD (+ running sha256)
            if u.error:
                self._upd_phase = "error"
                self._upd_msg = u.error
                return
            if not more:
                path = u.download_finish()     # close + verify size/sha256
                if u.error or not path:
                    self._upd_phase = "error"
                    self._upd_msg = u.error or "verify failed"
                    return
                self._upd_bin = (path, u.dl_total or u.dl_done)
                self._upd_phase = "confirm"    # hand off to the Phase-2 install confirm
        elif ph == "install":
            if u is None:
                self._upd_phase = "error"
                self._upd_msg = "no updater"
                return
            more = u.step()                    # one SD session: read + flash a chunk
            if u.error:
                self._upd_phase = "error"
                self._upd_msg = u.error
                return
            if not more:
                if u.finish():                 # point the bootloader at the new slot
                    self._upd_phase = "done"
                    self._upd_at = _ticks_ms()
                else:
                    self._upd_phase = "error"
                    self._upd_msg = u.error or "set_boot failed"
        elif ph == "done":
            # Brief pause so the kid sees "UPDATED!", then reboot into the new image.
            if _ticks_diff(_ticks_ms(), self._upd_at) >= 1200:
                try:
                    u.reset()
                except Exception:
                    self._upd_phase = "error"
                    self._upd_msg = "reset failed"

    def _draw_update(self, dt):
        """The firmware-update screen: confirm / progress / done / error. On the
        SYSTEM canvas, same panel chrome as Settings (host == device)."""
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        th = self.ws.theme_colors
        lay = self.ws.layout
        fs = lay.fs
        cv.rect(0, 0, lay.w, lay.h, th["bar"])
        px, py, pw, ph = lay.settings_panel
        cv.rect(px, py, pw, ph, th["surface"])
        cv.rectb(px, py, pw, ph, th["edge"])
        self.ws._glyph("gear", (px + 6, py + 2, 14 * fs, 14 * fs), th["accent"], cv)
        cv.print("UPDATE", px + 24, py + 4, th["ink"], 2)
        self.ws._mini_btn("X", lay.set_back, th["danger"], cv)
        u = self.ws.updater
        slot = u.slot() if u is not None else "?"
        ver = u.version() if u is not None else 0
        vlabel = u.version_label() if u is not None else "v0"
        x = px + 12 * fs
        y = py + 28 * fs
        phase = self._upd_phase
        if phase == "checking":
            cv.print("CHECKING ONLINE...", x, y, th["accent"], 1)
            y += 16 * fs
            beta = self.ws._ota_channel() == "unstable"
            cv.print("channel: %s" % ("BETA" if beta else "STABLE"), x, y,
                     NAMES["orange"] if beta else th["play"], 1)
            y += 14 * fs
            cv.print("running: %s %s" % (slot, vlabel), x, y, th["ink_dim"], 1)
        elif phase == "nopublish":
            beta = self.ws._ota_channel() == "unstable"
            cv.print("NOTHING NEW YET", x, y, th["play"], 1)
            y += 14 * fs
            cv.print("no %s build for" % ("BETA" if beta else "STABLE"),
                     x, y, th["ink"], 1)
            y += 12 * fs
            cv.print("this console yet.", x, y, th["ink"], 1)
            y += 14 * fs
            cv.print("running %s" % vlabel, x, y, th["ink_dim"], 1)
            y += 16 * fs
            cv.print("B = BACK", x, y, th["accent"], 1)
        elif phase == "uptodate":
            cv.print("UP TO DATE", x, y, th["play"], 1)
            y += 14 * fs
            cv.print("firmware %s" % vlabel, x, y, th["ink"], 1)
            y += 18 * fs
            cv.print("B = BACK", x, y, th["accent"], 1)
        elif phase == "confirm_online" and self._online_manifest:
            m = self._online_manifest
            newv = int(m.get("version", 0) or 0)
            kb = int(m.get("size", 0) or 0) // 1024
            run_ch = u.channel() if u is not None else "stable"
            tgt_ch = m.get("channel") or self.ws._ota_channel()
            label = str(m.get("label") or ("v%d" % newv))
            switch = tgt_ch != run_ch
            beta = tgt_ch == "unstable"
            tgt_name = "BETA" if beta else "STABLE"
            # Leading with the switch is deliberate: a kid who flipped the
            # channel by accident should meet that fact here, on the screen that
            # asks them to commit, rather than discover it after a reboot.
            cv.print("SWITCH TO %s" % tgt_name if switch else "UPDATE AVAILABLE",
                     x, y, th["ink_dim"], 1)
            y += 12 * fs
            # Always from -> to. The switch case used to show only the target, so
            # the one screen where the move matters most was the one that never
            # said what you were leaving -- which is what an accidental flip
            # needs to see.
            cv.print("%s ->" % vlabel[:20], x, y, th["ink_dim"], 1)
            y += 11 * fs
            cv.print(label[:22], x, y, NAMES["orange"] if beta else th["play"], 1)
            y += 13 * fs
            if kb:
                cv.print("%d KB download" % kb, x, y, th["ink"], 1)
                y += 14 * fs
            else:
                y += 2 * fs
            cv.print("A = DOWNLOAD", x, y, th["accent"], 1)
            y += 12 * fs
            cv.print("B = CANCEL", x, y, th["ink_dim"], 1)
        elif phase == "downloading":
            done = u.dl_done if u is not None else 0
            total = u.dl_total if (u is not None and u.dl_total) else 0
            cv.print("DOWNLOADING...", x, y, th["accent"], 1)
            y += 16 * fs
            frac = (done / total) if total else 0.0
            self._draw_progress_bar(px + 12 * fs, y, pw - 24 * fs, 10 * fs, frac)
            y += 16 * fs
            if total:
                cv.print("%d / %d KB" % (done // 1024, total // 1024), x, y, th["ink"], 1)
            else:
                cv.print("%d KB" % (done // 1024), x, y, th["ink"], 1)
            y += 16 * fs
            cv.print("B = CANCEL", x, y, th["ink_dim"], 1)
        elif phase == "confirm" and self._upd_bin:
            path, size = self._upd_bin
            name = path.rsplit("/", 1)[-1]
            cv.print("FOUND ON SD:", x, y, th["ink_dim"], 1)
            y += 12 * fs
            cv.print(name[:24], x, y, th["play"], 1)
            y += 12 * fs
            cv.print("%d KB" % (size // 1024), x, y, th["ink"], 1)
            y += 14 * fs
            cv.print("running: %s" % slot, x, y, th["ink_dim"], 1)
            y += 18 * fs
            cv.print("A = INSTALL", x, y, th["accent"], 1)
            y += 12 * fs
            cv.print("B = CANCEL", x, y, th["ink_dim"], 1)
        elif phase == "install":
            done = u.done if u is not None else 0
            total = u.total if (u is not None and u.total) else 1
            cv.print("FLASHING...", x, y, th["accent"], 1)
            y += 16 * fs
            self._draw_progress_bar(px + 12 * fs, y, pw - 24 * fs, 10 * fs, done / total)
            y += 16 * fs
            cv.print("%d / %d KB" % (done // 1024, (u.total // 1024) if u else 0),
                     x, y, th["ink"], 1)
            y += 16 * fs
            cv.print("DO NOT POWER OFF", x, y, th["danger"], 1)
        elif phase == "done":
            cv.print("UPDATED!", x, y, th["play"], 2)
            y += 20 * fs
            cv.print("rebooting...", x, y, th["ink"], 1)
        elif phase == "updated":
            # The verdict from the PREVIOUS boot: the slot we were pointed at is
            # the one now running. This is the only place the machine ever says
            # the update truly took -- "done" above is a hope, this is a fact.
            cv.print("IT WORKED!", x, y, th["play"], 2)
            y += 20 * fs
            cv.print("new firmware:", x, y, th["ink_dim"], 1)
            y += 12 * fs
            cv.print((self._upd_msg or "?")[:26], x, y, th["ink"], 1)
            y += 18 * fs
            cv.print("B = BACK", x, y, th["accent"], 1)
        elif phase == "rolledback":
            # Voice: the machine did the right thing and should say so plainly.
            # Nothing was lost, the kid did nothing wrong, and the old firmware
            # is exactly the one they had -- so the tone is reassurance, not alarm.
            cv.print("The new one didn't", x, y, th["danger"], 1)
            y += 12 * fs
            cv.print("start up.", x, y, th["danger"], 1)
            y += 14 * fs
            cv.print("I put your old one", x, y, th["ink"], 1)
            y += 12 * fs
            cv.print("back. Nothing lost.", x, y, th["ink"], 1)
            y += 14 * fs
            cv.print((self._upd_msg or "?")[:26], x, y, th["ink_dim"], 1)
            y += 16 * fs
            cv.print("B = BACK", x, y, th["accent"], 1)
        else:  # error
            # Voice: the rollback design means a failed update truly leaves the
            # running firmware untouched -- "Nothing changed." states that trust.
            cv.print("Update didn't finish.", x, y, th["danger"], 1)
            y += 12 * fs
            cv.print("Nothing changed.", x, y, th["ink"], 1)
            y += 12 * fs
            cv.print((self._upd_msg or "?")[:26], x, y, th["ink_dim"], 1)
            y += 18 * fs
            cv.print("B = BACK", x, y, th["accent"], 1)

    def _draw_progress_bar(self, x, y, w, h, frac):
        th = self.ws.theme_colors
        cv = self.ws.sys_canvas
        if frac < 0:
            frac = 0.0
        elif frac > 1:
            frac = 1.0
        cv.rectb(x, y, w, h, th["ink_dim"])
        fill = int((w - 2) * frac)
        if fill > 0:
            cv.rect(x + 1, y + 1, fill, h - 2, th["play"])

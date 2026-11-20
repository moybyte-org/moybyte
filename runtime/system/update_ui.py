# Map (grep -n a name to jump there):
#   UpdateUI                     the firmware-update screens: SD, online, C6
#   UpdateUI.open_update         the SD update screen
#   UpdateUI.open_update_online  the online update flow
#   UpdateUI.open_update_c6      the C6 radio upgrade
#   UpdateUI._pump_update        one frame of an install
#   UpdateUI._draw_update        the update screen's draw
"""The firmware-update (OTA) screen's UI layer (#53), extracted from Workstation
(runtime/shell/console.py).

It is KERNEL CHROME, not an app (owner decision 2026-11-01): it drives the
updater, firmware update is not a role (docs/app_api_v1.md), so it keeps the
console like the About card in shell_cards and takes no AppContext.

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
  * The page (`page`, a `uitree` build at the foot of this file) -- the
    phase's lines under a group caption, a download's or a flash's progress
    as a `ui.progress` row, and A's and B's verbs in the hints: two actions
    on the confirm and back roles (`go`, `leave`), each hint "" where its
    key names nothing. UpdateUI hands it the phase's lines through its
    `show` hook when they change, routes its own keys and taps to the two
    roles (`Engine.role`), and draws the bar's ground and the X around it.

Dependency profile (the facade lens, runtime/shell/system_api.py) -- through its
`self.ws` back-reference:
  * shared (non-privileged): ws.screen, ws.layout, ws.sys_canvas, ws.frame_state.dirty,
                             ws.home.go_home,
                             ws.toggles.ota_channel
  * privileged (draft make_system_api): ws.updater -- the OtaUpdater handle
                             (find_bin/begin/step/finish/check_online/
                             begin_download/download_step/reset). This is the one
                             genuinely privileged capability the screen needs; it
                             is injected onto Workstation by moy_runtime.run_desktop.

`err_text` is injected at construction (same circular-import
reason as BlockEditorUI: console.py builds the one UpdateUI a Workstation holds);
the rect hit-test is `ui.rect_in`, imported directly.
`_ticks_ms` / `_ticks_diff` are duplicated here (they only wrap `time`; the same
foundational-helper duplication BlockEditorUI's layout constants use), so the
method bodies stay byte-for-byte identical to the pre-extraction versions.
"""


import ui as _ui
import uitree as ui
import layout_base as _lb
from ui_draw import AppHost
from bar_layer import mini_btn as _bar_mini_btn
_in = _ui.rect_in   # one hit-test (ui.rect_in)

from ticks import _ticks_ms, _ticks_diff


def _dots():
    """One to three dots, stepping: a wait that is still running."""
    return "." * (1 + _ticks_ms() // 400 % 3)


class UpdateUI:
    def __init__(self, ws, err_text):
        self.ws = ws
        self._host = AppHost()        # the page's context; it takes no input
        # Injected instead of imported back from console.py (see module docstring).
        self._err_text = err_text
        # Update-screen transient state (was Workstation's; _updater_ok/_online_ok
        # stay there -- they back the queries, not the screen).
        self._upd_phase = None
        self._upd_msg = ""            # update screen: error / status text
        self._upd_bin = None          # update screen: (path, size) of the found/downloaded image
        self._upd_at = 0              # update screen: timestamp the install finished
        self._online_manifest = None  # the fetched update manifest dict
        # The version the last online check offered ("0.9"), which About's
        # Updates row says is ready; None after one found nothing newer.
        self.offered = None
        self._check_armed = False     # one-frame gate so CHECKING... draws before the blocking fetch
        self._pg = None               # the page's engine, made when the screen first opens or draws
        self._shown = None            # the screen the page shows (`_screen`)

    def _page(self):
        if self._pg is None:
            import uitree_app
            self._pg = uitree_app.hosted_page(page, self._host, _Damage(self.ws), env=self)
            self._pg.open()
        return self._pg

    def open_update(self):
        """Open the firmware-update screen: scan SD for an image to install. Lands on
        the "confirm" phase when one is found, else "error" with a friendly reason."""
        self.ws.wm.goto("update")     # Stage 6e: push the update screen onto the back-stack
        self.ws.mark_dirty()
        self._page().open()
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
        and if it's newer, stream the image into the inactive slot. The check
        runs in _pump_update one frame later so a CHECKING... screen shows first;
        the link's dial is polled there a frame at a time, so the screen keeps
        drawing and B cancels it."""
        self.ws.wm.goto("update")     # Stage 6e: push the update screen onto the back-stack
        self.ws.mark_dirty()
        self._page().open()
        self._upd_bin = None
        self._upd_msg = ""
        self._online_manifest = None
        if self.ws.updater is None:
            self._upd_phase = "error"
            self._upd_msg = "no updater"
            return
        if self._boot_verdict_phase():
            return
        self._addon_yields()
        self.ws.spine.wifi_hold("update")            # the radio lease: _exit_update lets go
        self._check_armed = False              # gate: draw CHECKING... before the blocking fetch
        self._upd_phase = "checking"

    def open_update_c6(self):
        """Open the C6 radio-upgrade flow (#7/#58): the same screen, pumped a
        step per frame, backed by ws.c6_updater (P4 only -- the row that opens
        this is capability-gated on it). check -> confirm -> download -> flash
        over SDIO -> reboot; the backend module's header carries the design."""
        self.ws.wm.goto("update")
        self.ws.mark_dirty()
        self._page().open()
        self._upd_bin = None
        self._upd_msg = ""
        if getattr(self.ws, "c6_updater", None) is None:
            self._upd_phase = "error"
            self._upd_msg = "no c6 updater"
            return
        self._addon_yields()
        self.ws.spine.wifi_hold("update")            # the same lease: the manifest rides the link
        self._check_armed = False              # CHECKING... paints first
        self._upd_phase = "c6_checking"

    def _addon_yields(self):
        """The browser console add-on's step (runtime/carts/web_addon.py) gives
        the kernel's updater and the "update" hold to this screen."""
        addon = getattr(self.ws, "addon", None)
        if addon is not None:
            addon.stop(release=False)

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
        """Open the socket and the inactive slot, and switch to the streaming
        download phase. Into the slot on every board: no filesystem space, and
        one write of each byte (the Guition S3's internal VFS cannot hold its
        own image)."""
        u = self.ws.updater
        if u is None or not self._online_manifest:
            return
        self.ws.mark_dirty()
        try:
            u.begin_download(self._online_manifest)
            self._upd_phase = "downloading"
        except Exception as exc:               # noqa: BLE001 -- shown to the kid
            self._upd_phase = "error"
            self._upd_msg = self._err_text(exc)[:30]

    def _exit_update(self):
        """Leave the update screen back to Settings, dropping any in-progress install
        or download (the inactive slot may be half-written, but it was never set
        bootable; the socket and any partial file are closed)."""
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
        cu = getattr(self.ws, "c6_updater", None)
        if cu is not None:
            try:
                cu.cancel()
            except Exception:
                pass
        self.ws.spine.wifi_release("update")        # the screen was the radio's reason
        addon = getattr(self.ws, "addon", None)
        if addon is not None:
            addon.start(retry=True)                 # it yielded to this screen
        if self._pg is not None:
            self._pg.close()
        self.ws.wm.goto("settings")   # Stage 6e: pop the update screen, back to Settings
        self.ws.mark_dirty()

    def _in_slot(self):
        """True when the image is already in the inactive slot (streamed there and
        verified), so installing it is pointing the bootloader at it."""
        staged = getattr(self.ws.updater, "staged_in_slot", None)
        return bool(staged is not None and staged())

    def _confirm_update(self):
        """Install the image: activate one already in the slot, else begin flashing
        the found file (validates header + size, opens the slot)."""
        u = self.ws.updater
        if u is None or not self._upd_bin:
            return
        self.ws.mark_dirty()
        if self._in_slot():
            if u.finish():
                self._upd_phase = "done"
                self._upd_at = _ticks_ms()
            else:
                self._upd_phase = "error"
                self._upd_msg = u.error or "set_boot failed"
            return
        try:
            u.begin(self._upd_bin[0])
            self._upd_phase = "install"
        except Exception as exc:               # noqa: BLE001 -- shown to the kid
            self._upd_phase = "error"
            self._upd_msg = self._err_text(exc)[:30]

    def _update_input(self, i):
        """The screen's keys: Home leaves (but past the point of no return);
        A and B are the page's confirm and back."""
        if i.pressed("home") or i.pressed("stop"):
            if self._upd_phase != "done":
                self._exit_update()
                self.ws.home.home_key()
        elif i.pressed("a") or i.pressed("run"):
            self._page().role("confirm")
        elif i.pressed("b"):
            self._page().role("back")

    def _update_pointer(self, px, py, click):
        if not click:
            return
        if _in(px, py, self.ws.layout.set_back):  # the X in the title row
            if self._upd_phase != "done":
                self._exit_update()
            return
        self._page().role("confirm")              # a tap anywhere else is A

    def _start_c6_download(self):
        cu = getattr(self.ws, "c6_updater", None)
        if cu is None or not cu.offer:
            return
        self.ws.mark_dirty()
        try:
            cu.begin_download()
            self._upd_phase = "c6_downloading"
        except Exception as exc:               # noqa: BLE001 -- shown to the kid
            self._upd_phase = "error"
            self._upd_msg = self._err_text(exc)[:30]

    def _pump_c6(self):
        """The C6 flow's per-frame step (called from _pump_update). Same shape
        as the firmware phases: one check behind its arm gate, asked again
        each frame while the link dials, then one chunk of download or flash
        per painted frame."""
        cu = getattr(self.ws, "c6_updater", None)
        ph = self._upd_phase
        if cu is None:
            self._upd_phase = "error"
            self._upd_msg = "no c6 updater"
            return
        if ph == "c6_checking":
            if not self._check_armed:
                self._check_armed = True
                return
            verdict = cu.check(self.ws.toggles.ota_channel())
            if verdict == "pending":
                return                         # the link is dialled: ask next frame
            if verdict == "offer":
                self._upd_phase = "c6_confirm"
            elif verdict == "uptodate":
                self._upd_phase = "c6_uptodate"
            elif verdict == "nopublish":
                self._upd_phase = "nopublish"
            else:
                self._upd_phase = "error"
                self._upd_msg = cu.error or "check failed"
        elif ph == "c6_downloading":
            more = cu.download_step()
            if cu.error or self.ws.updater.error:
                self._upd_phase = "error"
                self._upd_msg = cu.error or self.ws.updater.error
                return
            if not more:
                path = cu.download_finish()
                if not path:
                    self._upd_phase = "error"
                    self._upd_msg = cu.error or "verify failed"
                    return
                try:
                    cu.begin_flash(path)
                    self._upd_phase = "c6_flashing"
                except Exception as exc:       # noqa: BLE001 -- shown to the kid
                    self._upd_phase = "error"
                    self._upd_msg = self._err_text(exc)[:30]
        elif ph == "c6_flashing":
            more = cu.flash_step()
            if cu.error:
                self._upd_phase = "error"
                self._upd_msg = cu.error
                return
            if not more:
                if cu.finish_flash() and cu.activate():
                    self._upd_phase = "c6_done"
                    self._upd_at = _ticks_ms()
                else:
                    self._upd_phase = "error"
                    self._upd_msg = cu.error or "c6 flash failed"
        elif ph == "c6_done":
            # The C6 is rebooting under a host whose WiFi/BLE state is stale --
            # a full console restart re-runs the proven bring-up rather than
            # re-verifying against the exact npl minefield the D-tail mapped.
            if _ticks_diff(_ticks_ms(), self._upd_at) >= 1500:
                try:
                    self.ws.updater.reset()
                except Exception:
                    self._upd_phase = "error"
                    self._upd_msg = "reset failed"

    def _pump_update(self, dt):
        """Advance the install one chunk (called each painted frame on the update
        screen). Drives begin->step*N->finish->reset through the updater backend."""
        u = self.ws.updater
        ph = self._upd_phase
        if ph and ph.startswith("c6_"):
            self._pump_c6()
            return
        if ph == "checking":
            # The check starts ONE frame after entry, so the CHECKING... screen
            # paints first (this method runs before _draw each frame); the link
            # is polled each frame after, then the manifest's fetch blocks once.
            if not self._check_armed:
                self._check_armed = True
                return
            if u is None:
                self._upd_phase = "error"
                self._upd_msg = "no updater"
                return
            ch = self.ws.toggles.ota_channel()
            manifest = u.check_online(ch)      # connect (saved creds) + GET the manifest
            if u.error:
                self._upd_phase = "error"
                self._upd_msg = u.error
                return
            if getattr(u, "handed_off", False):
                # A REMOTE backend whose board has a screen of its own took
                # this onto that screen (firmware/web_runner/update_link.py).
                # Nothing installs here and nothing should pretend to: a
                # console advances the flash a chunk per PAINTED FRAME of this
                # screen, and the board's glass -- not this one -- is where
                # those frames are.
                self._upd_phase = "handed"
                return
            if getattr(u, "pending", False):
                # The backend has ASKED and not been answered: a local one is
                # dialling its link, a remote one's board has not replied.
                # Without the probe the first frame's empty answer would be
                # reported as "no manifest" against a console still looking.
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
                self.offered = None
                return
            self._online_manifest = manifest
            self.offered = str(manifest.get("label") or manifest.get("version") or "")
            self._upd_phase = "confirm_online"
        elif ph == "downloading":
            if u is None:
                self._upd_phase = "error"
                self._upd_msg = "no updater"
                return
            more = u.download_step()           # one chunk: socket -> slot (+ running sha256)
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
                self._upd_phase = "confirm"    # the second consent: A = INSTALL
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
                self._leave_every_surface()
                try:
                    u.reset()
                except Exception:
                    self._upd_phase = "error"
                    self._upd_msg = "reset failed"

    def _leave_every_surface(self):
        """A reboot is an exit path too (#154): every surface's edit is written
        before it (`Workstation.leave_every_surface`), an Editor window or an
        app open beside this screen with an edit inside its idle-debounce
        window among them. Guarded: a store hiccup must not strand a board
        whose bootloader already points at the new slot."""
        try:
            self.ws.leave_every_surface()
        except Exception as exc:  # noqa: BLE001
            print("Moybyte pre-reboot commit failed:", exc)

    def _screen(self):
        """What the phase says: the group's caption, its lines (a download's
        or a flash's progress as `BAR`, its fraction beside it), and the
        verbs A and B name in the hints, "" where a key names none."""
        u = self.ws.updater
        slot = u.slot() if u is not None else "?"
        vlabel = u.version_label() if u is not None else "v0"
        phase = self._upd_phase
        out = []
        say = out.append
        go = leave = ""
        if phase == "checking":
            say("CHECKING ONLINE...")
            beta = self.ws.toggles.ota_channel() == "unstable"
            say("channel: %s" % ("BETA" if beta else "STABLE"))
            say("running: %s %s" % (slot, vlabel))
            if getattr(u, "joining", False):
                say("joining WiFi" + _dots())
            leave = "Back"
        elif phase == "nopublish":
            beta = self.ws.toggles.ota_channel() == "unstable"
            say("NOTHING NEW YET")
            say("no %s build for" % ("BETA" if beta else "STABLE"))
            say("this console yet.")
            say("running %s" % vlabel)
            leave = "Back"
        elif phase == "handed":
            # The browser asked a console WITH GLASS to update. It gave the
            # glass back and opened this same screen THERE, so the only useful
            # thing to say here is where to look -- and that this page has
            # stopped being that console's screen.
            say("ON THE CONSOLE NOW")
            say("It took the update")
            say("onto its own screen.")
            say("Finish it there.")
            leave = "Back"
        elif phase == "uptodate":
            say("UP TO DATE")
            say("firmware %s" % vlabel)
            leave = "Back"
        elif phase == "confirm_online" and self._online_manifest:
            m = self._online_manifest
            newv = int(m.get("version", 0) or 0)
            kb = int(m.get("size", 0) or 0) // 1024
            run_ch = u.channel() if u is not None else "stable"
            tgt_ch = m.get("channel") or self.ws.toggles.ota_channel()
            label = str(m.get("label") or ("v%d" % newv))
            # Leading with the switch is deliberate: a kid who flipped the
            # channel by accident should meet that fact here, on the screen that
            # asks them to commit, rather than discover it after a reboot.
            say("SWITCH TO %s" % ("BETA" if tgt_ch == "unstable" else "STABLE")
                if tgt_ch != run_ch else "UPDATE AVAILABLE")
            # Always from -> to: what an accidental flip needs to see is what
            # it is leaving.
            say("%s ->" % vlabel[:20])
            say(label[:22])
            if kb:
                say("%d KB download" % kb)
            go, leave = "Download", "Cancel"
        elif phase == "downloading":
            done = u.dl_done if u is not None else 0
            total = u.dl_total if (u is not None and u.dl_total) else 0
            say("DOWNLOADING...")
            say(BAR)
            say((done / total) if total else 0.0)
            if total:
                say("%d / %d KB" % (done // 1024, total // 1024))
            else:
                say("%d KB" % (done // 1024))
            leave = "Cancel"
        elif phase == "confirm" and self._upd_bin:
            path, size = self._upd_bin
            if self._in_slot():
                head = "DOWNLOADED:"
                name = str((self._online_manifest or {}).get("label") or "firmware")
            else:
                head, name = "FOUND ON SD:", path.rsplit("/", 1)[-1]
            say(head)
            say(name[:24])
            say("%d KB" % (size // 1024))
            say("running: %s" % slot)
            go, leave = "Install", "Cancel"
        elif phase == "install":
            done = u.done if u is not None else 0
            total = u.total if (u is not None and u.total) else 1
            say("FLASHING...")
            say(BAR)
            say(done / total)
            say("%d / %d KB" % (done // 1024, (u.total // 1024) if u else 0))
            say("DO NOT POWER OFF")
        elif phase == "done":
            say("UPDATED!")
            say("rebooting...")
        elif phase == "c6_checking":
            cu = getattr(self.ws, "c6_updater", None)
            iv = cu.installed if cu is not None else None
            say("CHECKING C6 RADIO...")
            say("radio: %s" % (("shim v%d" % iv) if iv else "no espnow shim"))
            if getattr(u, "joining", False):
                say("joining WiFi" + _dots())
            leave = "Back"
        elif phase == "c6_confirm":
            cu = getattr(self.ws, "c6_updater", None)
            c6 = (cu.offer or {}) if cu is not None else {}
            iv = cu.installed if cu is not None else None
            say("UPGRADE C6 RADIO")
            say("%s ->" % (("shim v%d" % iv) if iv else "no espnow"))
            say("shim v%d (%s)" % (int(c6.get("version") or 0), str(c6.get("hosted") or "?")[:12]))
            kb = int(c6.get("size") or 0) // 1024
            if kb:
                say("%d KB download" % kb)
            go, leave = "Upgrade", "Cancel"
        elif phase == "c6_uptodate":
            cu = getattr(self.ws, "c6_updater", None)
            iv = cu.installed if cu is not None else None
            say("C6 RADIO UP TO DATE")
            say(("shim v%d" % iv) if iv else "?")
            leave = "Back"
        elif phase == "c6_downloading":
            cu = getattr(self.ws, "c6_updater", None)
            done = cu.dl_done if cu is not None else 0
            total = cu.dl_total if (cu is not None and cu.dl_total) else 0
            say("DOWNLOADING C6...")
            say(BAR)
            say((done / total) if total else 0.0)
            say("%d / %d KB" % (done // 1024, total // 1024))
            leave = "Cancel"
        elif phase == "c6_flashing":
            cu = getattr(self.ws, "c6_updater", None)
            done = cu.fl_done if cu is not None else 0
            total = cu.fl_total if (cu is not None and cu.fl_total) else 1
            say("FLASHING C6 RADIO...")
            say(BAR)
            say(done / total)
            say("%d / %d KB" % (done // 1024, total // 1024))
            say("DO NOT POWER OFF")
        elif phase == "c6_done":
            say("C6 UPDATED!")
            say("restarting...")
        elif phase == "updated":
            # The verdict from the PREVIOUS boot: the slot we were pointed at is
            # the one now running. This is the only place the machine ever says
            # the update truly took -- "done" above is a hope, this is a fact.
            say("IT WORKED!")
            say("new firmware:")
            say((self._upd_msg or "?")[:26])
            leave = "Back"
        elif phase == "rolledback":
            # Voice: the machine did the right thing and should say so plainly.
            # Nothing was lost, the kid did nothing wrong, and the old firmware
            # is exactly the one they had -- so the tone is reassurance, not alarm.
            say("The new one didn't")
            say("start up.")
            say("I put your old one")
            say("back. Nothing lost.")
            say((self._upd_msg or "?")[:26])
            leave = "Back"
        else:  # error
            # Voice: the rollback design means a failed update truly leaves the
            # running firmware untouched -- "Nothing changed." states that trust.
            say("Update didn't finish.")
            say("Nothing changed.")
            say((self._upd_msg or "?")[:26])
            leave = "Back"
        cap = "C6 radio" if phase and phase.startswith("c6_") else "Firmware update"
        return cap, tuple(out), go, leave

    def _draw_update(self, dt):
        """The firmware-update screen: the bar's ground, the page (`page`,
        the phase's lines handed to its `show` hook when they change) in the
        content rect, and the X in the title row."""
        ws = self.ws
        cv = ws.sys_canvas
        th = ws.theme_colors
        lay = ws.layout
        screen = self._screen()
        if screen != self._shown:
            self._shown = screen
            pg = self._page()
            pg.run_action(pg.actions["show"], (screen,), "the update screen")
        cv.rect(0, 0, lay.w, lay.h, th["bar"])      # the screen has no bar of its own
        rect = (lay.cx, lay.cy, lay.cw, lay.ch)
        self._host.begin(cv, th, rect)
        self._page().paint(dt, cv, th, rect, _lb.metric_class() == _lb.DESK)
        if not ws.draw_icon("close", lay.set_back, th["ink"], cv):    # the theme's set, else
            _bar_mini_btn("X", lay.set_back, th["danger"], cv, lay.tap_box(lay.set_back, 18, 14))


class _Damage:
    """The page's damage: the console's one dirty flag."""

    def __init__(self, ws):
        self.ws = ws

    def all(self):
        self.ws.mark_dirty()


# -- the page -------------------------------------------------------------------

BAR = None                      # in a screen's lines: the progress bar, its fraction next

s = ui.state(cap="", head=ui.model(key="n", n="", text=""), bar=False, frac=0,
             tail=ui.model(key="n", n="", text=""), go_word="", leave_word="")


# B leaves these; A leaves the last seven too
_LEAVE_ANY = ("error", "uptodate", "updated", "rolledback", "nopublish", "handed", "c6_uptodate")
_LEAVES = ("confirm", "confirm_online", "c6_confirm", "install", "downloading", "checking",
           "c6_checking", "c6_downloading", "c6_flashing") + _LEAVE_ANY


def show(screen):
    cap, lines, go_, leave_ = screen
    s.cap, s.go_word, s.leave_word = cap, go_, leave_
    at = lines.index(BAR) if BAR in lines else len(lines)
    s.bar = at < len(lines)
    s.frac = int(max(0.0, min(1.0, lines[at + 1])) * 1000) if s.bar else 0
    s.head = [{"n": str(i), "text": t} for i, t in enumerate(lines[:at])]
    s.tail = [{"n": str(i), "text": t} for i, t in enumerate(lines[at + 2:])]


def _owner():
    """The UpdateUI whose screen the running handler's page is."""
    import uitree_app
    return uitree_app.env()


def go():
    """A, or a tap anywhere but the X: what the phase offers, else leave
    where A leaves."""
    u = _owner()
    ph = u._upd_phase
    if ph == "confirm":
        u._confirm_update()
    elif ph == "confirm_online":
        u._start_download()
    elif ph == "c6_confirm":
        u._start_c6_download()
    elif ph in _LEAVE_ANY:
        u._exit_update()


def leave():
    """B: back to Settings, dropping an install or download under way;
    "done" is past the point of no return."""
    u = _owner()
    if u._upd_phase in _LEAVES:
        u._exit_update()


ui.hook("show", show, takes="arg")
ui.action("go", "Go", go, keys=("role:confirm",), hint="{go_word}")
ui.action("leave", "Back", leave, keys=("role:back",), hint="{leave_word}")


def page():
    with ui.page(""):
        with ui.group("{cap}"):
            with ui.rows("head"):
                ui.label("{.text}")
            ui.progress("frac", max=1000, label="Done", shown="bar")
            with ui.rows("tail"):
                ui.label("{.text}")

"""The Workstation's SETTINGS verbs: the `SETTINGS_TOGGLES` setters
(`set_diag_live`/`set_diag_sd`/`set_steady`/`set_two_player`/`set_crisp_pixels`/
`set_show_fps`) over the one `_set_toggle` tail, the second-keyboard probe local
2P rides on, and the OTA channel choice. A mixin of `Workstation`: each verb
writes the flat mirror, marks the repaint and persists through `prefs`. What
the settings MEAN at boot (`load_system`'s apply cascade) stays on the kernel.
"""


class SettingsToggles:

    def _set_toggle(self, key, on, persist):
        """The tail every SETTINGS_TOGGLES verb shares: the flat mirror, the
        repaint mark, and the persisted copy under the SAME name (#209 section
        7). What differs per toggle -- the phase reset, the canvas hook, the
        keyboard hand-over -- stays written out in the verb that calls this.

        The mirror is set with setattr, which is fine because this runs on a
        FLIP and at boot, never per frame; the READ side stays a plain
        attribute everywhere, and must -- both WMs read show_fps on every
        painted game frame on all three boards."""
        setattr(self, key, on)
        self._dirty = True
        if persist:
            self.system[key] = on
            self.prefs.persist()

    def set_diag_live(self, on, persist=True):
        """Flip the #68 diagnostics gate (Settings -> PERF DIAG) and persist it.
        The device loop (moy_runtime.run_desktop) reads self.diag_live each cycle,
        so the change takes effect within a frame -- no reboot."""
        self._set_toggle("diag_live", bool(on), persist)

    def set_diag_sd(self, on, persist=True):
        """Flip the periodic diag->SD write gate (Settings -> DIAG SD LOG) and
        persist it. Separate from PERF DIAG so a measurement session can stream
        serial samples WITHOUT the ~115ms 20s sdflush stutter; the offline
        play-then-read-diag.log workflow flips this ON too. Crash/cart-exit
        flushes are unconditional either way (the safety net)."""
        self._set_toggle("diag_sd", bool(on), persist)

    def set_steady(self, on, persist=True):
        """Flip the tick model's STEADY / FREE knob (#217, Settings -> STEADY)
        and persist it: how long the draw divisor remembers before it decides
        again. Relayed live into the running cart's scheduler."""
        self._set_toggle("steady", bool(on), persist)
        pl = getattr(self, "player", None)
        if pl is not None:
            pl.steady_mode(on)

    def second_keyboard(self):
        """The keyboard that can become player two, or None.

        A board qualifies only when it has a SECOND keyboard: the T-Deck's
        paired Bluetooth one, alongside the physical C3 keyboard it already
        has. On the touch-only boards a BLE keyboard IS `ws.keyboard` -- the
        only one there is -- so handing it to player two would leave player one
        with nothing to press."""
        ble = getattr(self, "ble_keyboard", None)
        if ble is None or ble is self.keyboard:
            return None
        return ble if getattr(ble, "set_player", None) is not None else None

    def set_two_player(self, on, persist=True):
        """Flip LOCAL 2P (Settings -> 2 PLAYERS) and persist it.

        Two kids, two real keyboards, one screen, and no radio between consoles.
        The whole mechanism is that the second keyboard's input SOURCE carries a
        player: a source with a player IS a player (#26), so `players()` reports
        2 and every cart that offers a 2P mode finds it -- no transport, no
        session, no netcode.

        A board with no second keyboard reports OFF whatever it is told. Saying
        otherwise would be the frozen-meter bug in another costume: the console
        would claim two players while nothing on it could produce the second
        one's buttons. (The keyboard slot stays honest at the other end too --
        an UNCONNECTED Bluetooth keyboard does not hold the slot, or the cart
        would field a character nobody could move.)"""
        on = bool(on)
        kb = self.second_keyboard()
        if kb is None:
            on = False
        else:
            try:
                kb.set_player(1 if on else 0)
            except Exception as exc:  # noqa: BLE001 -- a keyboard hiccup is not a crash
                print("Moybyte 2 players failed:", exc)
                on = False
        self._set_toggle("two_player", on, persist)

    def set_crisp_pixels(self, on, persist=True):
        """Flip the CRISP PIXELS composite (Settings row, capability-gated) and
        persist it. The mode lives on the SYSTEM canvas (set_crisp_scale --
        the P4's P4SystemCanvas routes the game composite nearest-neighbour
        instead of the PPA's fixed-bilinear scaler); a canvas without the hook
        never shows the row, so this setter is then only ever the boot apply
        of a stale system.json key."""
        on = bool(on)
        self._set_toggle("crisp_pixels", on, persist)
        hook = getattr(self.sys_canvas, "set_crisp_scale", None)
        if hook is not None:
            hook(on)

    def set_show_fps(self, on, persist=True):
        """Flip the in-game FPS chip (Settings -> SHOW FPS) and persist it.
        The chip is GAME-domain (it rides the cart's canvas and its composite
        scale -- 2x-big on a 128px cart, and fold-compatible for free, #190),
        so hiding it is purely cosmetic: the perf fields keep updating and
        PERF DIAG is untouched. Hiding also disables the chip's tap-to-toggle
        breakdown HUD, so clear that too rather than strand it on-screen."""
        on = bool(on)
        if not on:
            self.perf_hud = False
        self._set_toggle("show_fps", on, persist)

    def _ota_channel(self):
        """The selected OTA update channel ("stable" / "unstable" beta). Drives which
        manifest UPDATE ONLINE checks; persisted in system.json once chosen.

        The default is the channel this FIRMWARE was built on, not a constant. A
        board that took a beta is running `unstable`, and defaulting it to
        `stable` meant every check compared the two, found them different, and
        offered the "update" -- a downgrade, on every check, forever, because
        installing it is the only thing that would make the two agree. Which
        channel you are on is a fact about the running image; the setting is a
        deliberate departure from it, so absence of a setting should mean "the
        one I am on"."""
        saved = self.system.get("ota_channel")
        if saved in ("stable", "unstable"):
            return saved
        u = self.updater
        if u is not None:
            try:
                running = u.channel()
                if running in ("stable", "unstable"):
                    return running
            except Exception:            # a backend without channel(): fall through
                pass
        return "stable"

    def _cycle_channel(self, d):
        """Toggle the OTA channel STABLE<->UNSTABLE and persist. Two channels, so any
        step flips. This only changes what UPDATE ONLINE checks -- the running firmware
        is unchanged until a manifest is actually installed (and the bootloader's
        rollback still guards a bad beta image)."""
        self.system["ota_channel"] = (
            "stable" if self._ota_channel() == "unstable" else "unstable")
        self.prefs.persist()

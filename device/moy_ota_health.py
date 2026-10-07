# Map (grep -n a name to jump there):
#   SlotHealth                       did the update work: the verdict and the confirm
#   SlotHealth.boot_check            read the last install's marker
#   SlotHealth.confirm_when_healthy  the rollback confirm, after painted frames
#   SlotHealth.confirm_when_serving  the same, for a board with no glass
"""The OTA's health half (#53): did the last update take, and is the running
image good enough to keep.

The boot reads the verdict the previous install left (`boot_check`), and the
frame loop confirms the running image once it has proved itself
(`confirm_when_healthy`, or `confirm_when_serving` on the Zero) -- after which
the bootloader no longer rolls it back. That is the frame loop's business, not
the updater's, so it is its own file: device/moy_ota.py's `OtaUpdater` writes
images into the inactive slot and takes this class as its base, and the
kernel's native loop replaces this file (docs/kernel_survival_2026-10.md
section 7.2).

`with_sd(fn)` runs fn() inside the board's store session, as the updater's
does; the pending marker lives in `update_dir`. The running build's label
(`version_label`) is the updater's, which is why boot_check reads it off self.
"""

# What counts as proof that the update worked (confirm_when_healthy). The
# rollback net can only revert an image that never SAID it was fine, so what that
# claim is worth is decided entirely by where it is made -- and "the desktop
# object was constructed" is worth very little. Two conditions, because either
# alone is satisfied by a board nobody would call healthy:
#
#   HEALTHY_PAINTS -- frames that reached the GLASS. This is the one that catches
#       #56, where every boot print appeared and the panel stayed dark. It is 1
#       and cannot be more: the console repaints only when something changes, so
#       an idle desktop paints once and then sits there for as long as you like.
#       (Measured on the P4: 1 painted frame in the first 6 seconds. A threshold
#       of 120 painted frames -- the obvious first guess -- would have left every
#       board on a quiet desktop unconfirmed, i.e. rolled every update back.)
#   HEALTHY_LOOPS -- iterations of the frame loop survived after that. ~2-4s of
#       input polling, timers and compositing on either board; long enough that a
#       crash in the ordinary run of things lands inside it, short enough that
#       nobody power-cycles before it.
#   HEALTHY_SERVES -- the HEADLESS twin of the pair above (the Zero, 2026-08-29).
#       That board paints nothing ever, so a paint threshold there is not a weak
#       gate, it is an unreachable one: every update would roll back. What plays
#       the part of "something reached the glass" is the only externally visible
#       thing this board does -- its store host is up and listening on a joined
#       network -- and the part of HEALTHY_LOOPS is played by surviving this many
#       poll iterations afterwards. 300 rather than 120 because that loop's duty
#       cycle is a 10ms sleep, so 120 would be ~1.2s; 300 is ~3s, inside the same
#       "a crash in the ordinary run of things lands here" window the frame-loop
#       number was chosen for.
HEALTHY_PAINTS = 1
HEALTHY_LOOPS = 120
HEALTHY_SERVES = 300
PENDING_NAME = "pending.json"   # written into update_dir at finish(), read at boot


class SlotHealth:
    """The running slot's verdict and its confirm. Constructed by OtaUpdater."""

    def __init__(self, with_sd, update_dir):
        self.update_dir = update_dir
        self._with_sd = with_sd
        self.confirmed = False   # has confirm_when_healthy already fired this boot?
        self._pending_seen = False   # boot_check found a marker -> the confirm clears it
        self._loops = 0           # frame-loop iterations it has been called from
        self.boot_verdict = None  # ("ok"|"rolled_back", text) from the previous install

    def _running_label(self):
        import esp32

        return esp32.Partition(esp32.Partition.RUNNING).info()[4]

    def slot(self):
        """The running partition label (ota_0 / ota_1 / factory) for display."""
        try:
            return self._running_label()
        except Exception:
            return "?"

    def mark_valid(self):
        """Confirm the running image is healthy so the bootloader cancels its pending
        rollback. The raw verb -- callers want confirm_when_healthy, which decides
        WHEN this is honest. No-op (swallowed) when the image was already marked
        valid or this isn't an OTA build."""
        try:
            import esp32

            esp32.Partition.mark_app_valid_cancel_rollback()
            return True
        except Exception:
            return False

    def confirm_when_healthy(self, frames_drawn):
        """The rollback confirm, deferred until the console has actually RUN.

        Marking the image valid where the desktop is CONSTRUCTED confirms firmware
        that has never drawn a pixel -- and a live board showing a black screen is
        exactly the failure this project has already shipped once (#56: every boot
        print appeared, the panel stayed dark). Rollback cannot save a board from a
        fault the firmware promised in advance would not happen.

        So the frame loop calls this every iteration with ws._frames_drawn, and the
        confirm waits for both halves of "it works": something reached the glass
        (HEALTHY_PAINTS) and the loop kept running afterwards (HEALTHY_LOOPS, which
        this counts itself -- one call per iteration is exactly what the loop makes
        it). An image that comes up mute, or comes up and then dies, is never
        confirmed, so the next reset reverts it. Returns True the one frame it
        confirms."""
        if self.confirmed:
            return False
        self._loops += 1
        if frames_drawn < HEALTHY_PAINTS or self._loops < HEALTHY_LOOPS:
            return False
        return self._confirm()

    def confirm_when_serving(self, serving):
        """The same confirm, for a board with no glass (the Zero, #41).

        `confirm_when_healthy` asks two questions -- did anything reach the
        display, and did the loop keep running -- and on a headless board the
        first one has no answer. Answering it with a constant would certify
        every image unconditionally; leaving it at zero would roll every image
        back. So the caller supplies the evidence its own hardware can give:
        `serving` is True while the store host is up and listening on a joined
        network, which is the only thing about this board a human outside it
        could ever observe. The loop half is unchanged in spirit and counted
        here, one call per poll iteration (HEALTHY_SERVES).

        Deliberately NOT a `frames_drawn=1` call into the method above: that
        would put a lie in the argument list, and the next reader would have to
        work out that a board with no panel was claiming a painted frame.
        Returns True the one iteration it confirms."""
        if self.confirmed:
            return False
        if not serving:
            # Not a countdown pause: an image whose host never comes up is
            # exactly the image the bootloader should take back, so the counter
            # only advances while the thing being certified is working.
            self._loops = 0
            return False
        self._loops += 1
        if self._loops < HEALTHY_SERVES:
            return False
        return self._confirm()

    def _confirm(self):
        """Mark the running image valid and retire the pending marker. Both
        confirm gates above end here, so "what confirming DOES" is one body and
        only "when is it honest" differs per board."""
        self.confirmed = True
        ok = self.mark_valid()
        # The pending marker is cleared HERE and not where it was read, so that an
        # image which boots, reports its verdict and then dies still has a marker
        # on the boot after the rollback -- otherwise that second failure would be
        # the silent one. Only when boot_check actually SAW one, though: on the
        # T-Deck this is an SD session on the bus the panel shares, and an
        # ordinary boot (no update pending, which is nearly all of them) should
        # not pay for a delete that can only fail.
        if self._pending_seen:
            self._pending_seen = False
            self._clear_pending()
        return ok

    def _pending_path(self):
        return self.update_dir + "/" + PENDING_NAME

    def _clear_pending(self):
        def _rm():
            import os

            try:
                os.remove(self._pending_path())
            except OSError:
                pass

        try:
            self._with_sd(_rm)
        except Exception:
            pass

    def boot_check(self):
        """Read the marker the previous install left and say what became of it.

        Returns None on an ordinary boot, else ("ok", text) when the slot we were
        pointed at is the one now running, or ("rolled_back", text) when it isn't --
        which means the bootloader gave up on the new image and put the old one
        back. The marker is deliberately NOT deleted here (see
        confirm_when_healthy). Also caches the verdict on `boot_verdict` for the
        update screen."""
        def _read():
            import json

            try:
                f = open(self._pending_path(), "r")
            except OSError:
                return None
            try:
                return json.load(f)
            finally:
                f.close()

        try:
            rec = self._with_sd(_read)
        except Exception:
            return None               # no SD / torn file: no verdict, same as before
        if not isinstance(rec, dict):
            return None
        self._pending_seen = True
        was = rec.get("label") or ("v%s" % rec.get("version", "?"))
        if rec.get("slot") == self.slot():
            self.boot_verdict = ("ok", "%s -> %s" % (was, self.version_label()))
        else:
            self.boot_verdict = ("rolled_back", "put %s back" % self.version_label())
        return self.boot_verdict

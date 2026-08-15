"""Moybyte T-Deck (mainline) boot shell: pick a boot mode and run it.

ONE STRING, NOT SIX BOOLEANS. The fork build's shell carries a flag per mode
(RUN_DESKTOP / RUN_TOUCH_CALIBRATE / RUN_KEYBOARD_PROBE) and an if-ladder that
silently prefers whichever it tests first, so two flags left on is a state with
no error and a surprising answer. This port brings up six subsystems in six
stages, which would have been six flags; `MODE` is the same information with
the mutual exclusion built in, and it reads the same over the REPL:

    import moybyte_shell as s; s.MODE = "touch"; s.main()

Every mode except "desktop" is SELF-TERMINATING -- it paints, prints and returns
to the REPL rather than taking the loop over. That is not tidiness. Under the
fork build this board's USB-CDC RX dies the moment a takeover loop starts (see
CLAUDE.md's hard constraints), and whether mainline's CDC stack has the same
hole is one of the questions this port exists to answer, so a bring-up program
must never be the thing that spends a REPL the owner might still have had.
"""

# The mode this image boots. Set by hand as the port advances; stage 6 makes
# "desktop" the default and the rest stay reachable from the REPL.
MODE = "touch"

MODES = ("panel", "touch", "desktop")


def main():
    print("Moybyte T-Deck (mainline) shell starting -- mode=%s" % MODE)
    if MODE == "desktop":
        try:
            from moy_runtime import run_desktop
            run_desktop()
        except KeyboardInterrupt:
            print("Moybyte desktop interrupted -> REPL")
        except Exception as exc:        # noqa: BLE001 -- say what broke, keep the REPL
            print("Moybyte desktop FAILED:", exc)
        return
    if MODE not in MODES:
        print("Moybyte: unknown MODE %r (expected one of %s) -> REPL"
              % (MODE, ", ".join(MODES)))
        return
    try:
        import tdeck_smoke
        getattr(tdeck_smoke, MODE)()
    except Exception as exc:            # noqa: BLE001 -- a failed smoke is a RESULT
        # Printed, never re-raised: the traceback would land on the same serial
        # line either way, and returning cleanly leaves the REPL usable for the
        # follow-up question ("moy_lcd.set_madctl(0x28)", "device_input.TOUCH_FLIP_X
        # = True; tdeck_smoke.touch()") which is the whole point of a smoke.
        print("Moybyte %s smoke FAILED: %s: %s" % (MODE, type(exc).__name__, exc))

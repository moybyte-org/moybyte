"""The boot shell's mode ladder -- one body, every console board.

ONE STRING, NOT SIX BOOLEANS. A flag per mode (`RUN_DESKTOP` /
`RUN_PANEL_SMOKE` / `RUN_KEYBOARD_PROBE`) plus an if-ladder that silently
prefers whichever it tests first makes "two flags left on" a state with no error
and a surprising answer. `MODE` is the same information with the mutual
exclusion built in, and it reads the same over the REPL:

    import moybyte_shell as s; s.MODE = "touch"; s.main()

Every mode except "desktop" is SELF-TERMINATING -- it paints, prints and returns
to the REPL rather than taking the loop over. That is not tidiness: a bring-up
program must never be the thing that spends a REPL the owner might still have
had, which is the whole point of a smoke.

A board declares three things and takes the rest: its name (the one string in
the prints), the ladder of modes it actually has, and the module its smokes live
in. That last is a NAME rather than an import, so a board pays for its smoke
module only in the boot that runs one.
"""


def main(board, mode, modes, smoke):
    """Run `mode` for `board`. Called by each board's `moybyte_shell.main()`."""
    print("Moybyte %s shell starting -- mode=%s" % (board, mode))
    if mode == "desktop":
        try:
            from moy_runtime import run_desktop
            run_desktop()
        except KeyboardInterrupt:
            print("Moybyte desktop interrupted -> REPL")
        except Exception as exc:        # noqa: BLE001 -- say what broke, keep the REPL
            print("Moybyte desktop FAILED:", exc)
        return
    if mode not in modes:
        print("Moybyte: unknown MODE %r (expected one of %s) -> REPL"
              % (mode, ", ".join(modes)))
        return
    try:
        getattr(__import__(smoke), mode)()
    except Exception as exc:            # noqa: BLE001 -- a failed smoke is a RESULT
        # Printed, never re-raised: the traceback would land on the same serial
        # line either way, and returning cleanly leaves the REPL usable for the
        # follow-up question ("moy_lcd.set_madctl(0x28)", "device_input.TOUCH_FLIP_X
        # = True; tdeck_smoke.touch()") which is the whole point of a smoke.
        print("Moybyte %s smoke FAILED: %s: %s" % (mode, type(exc).__name__, exc))

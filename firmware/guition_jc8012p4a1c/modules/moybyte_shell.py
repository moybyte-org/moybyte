"""Moybyte Guition P4 boot shell: pick a boot mode and run it.

The Guition S3's ONE-STRING arrangement. Every mode except "desktop" is
SELF-TERMINATING (paints, prints, returns to the REPL), and this board's REPL
-- the P4's own USB-Serial/JTAG -- stays alive under all of them:

    import moybyte_shell as s; s.MODE = "touch"; s.main()
"""

# The mode this image boots. "desktop" from stage 0: the smokes below stay
# reachable from the REPL, and the console is what a flashed board is for.
MODE = "desktop"

MODES = ("panel", "touch", "desktop")


def main():
    print("Moybyte Guition P4 shell starting -- mode=%s" % MODE)
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
        import guition_p4_smoke
        getattr(guition_p4_smoke, MODE)()
    except Exception as exc:            # noqa: BLE001 -- a failed smoke is a RESULT
        print("Moybyte %s smoke FAILED: %s: %s" % (MODE, type(exc).__name__, exc))

"""Moybyte Guition S3 boot shell: pick a boot mode and run it.

The T-Deck's ONE-STRING arrangement, verbatim in spirit (its docstring carries
the argument against per-mode booleans). This board's bring-up ladder is
shorter -- no keyboard stage, no SD stage yet, no audio stage yet -- so MODES
lists what exists. Every mode except "desktop" is SELF-TERMINATING (paints,
prints, returns to the REPL), and this board's REPL is expected to stay alive
under all of them (#201's console arrangement, baked in from stage 0):

    import moybyte_shell as s; s.MODE = "touch"; s.main()
"""

# The mode this image boots. Flipped to "desktop" 2026-08-18, the night the
# ladder below it passed on glass (panel first light, touch controller
# answering, console booting + running carts in both runtimes over the dev
# channel); the smokes stay reachable from the REPL.
MODE = "desktop"

MODES = ("panel", "touch", "desktop")


def main():
    print("Moybyte Guition S3 shell starting -- mode=%s" % MODE)
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
        import guition_smoke
        getattr(guition_smoke, MODE)()
    except Exception as exc:            # noqa: BLE001 -- a failed smoke is a RESULT
        print("Moybyte %s smoke FAILED: %s: %s" % (MODE, type(exc).__name__, exc))

# Auto-boot the Zero's headless store host. Guarded so ANY failure falls to
# the REPL instead of a reset loop -- a headless board whose only interface is
# this USB port must never wedge itself out of reach.
try:
    import zero_host
    zero_host.serve()
except KeyboardInterrupt:
    pass
except Exception as exc:                 # noqa: BLE001
    print("ZERO boot failed:", exc)

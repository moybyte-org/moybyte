# Auto-boot the Zero's headless store host (device/zero_host.py), the XIAO
# Zero's main.py on a C3. Guarded so ANY failure falls to the REPL instead of a
# reset loop -- a headless board whose only interface is this USB port must
# never wedge itself out of reach.
#
# The VM reaching this file is the boot's proof to the kernel (moy_kernel's
# boot_ok): a boot that ends without it restarts into the serial floor.
try:
    import moy_kernel
    moy_kernel.boot_ok()
except ImportError:
    pass
try:
    import zero_host
    zero_host.serve()
except KeyboardInterrupt:
    pass
except Exception as exc:                 # noqa: BLE001
    print("ZERO boot failed:", exc)

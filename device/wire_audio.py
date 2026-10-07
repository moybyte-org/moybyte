"""Audio's provider (docs/kernel_survival_2026-10.md section 2 item 1): what a
board's boot does for the speaker, which is nearly nothing. The kernel's
`moy_audio` plays the focused session from its own feeder task, started at
the first session's focus, never at boot; there is no fallback feed, and a
board whose output cannot start has no audio (`moy_audio.out()` says why).

`attach()` runs before the touch driver opens the I2C bus: on a board with a
codec it puts the codec's address on the kernel's bus (native/moy_kernel's
moy_bus), so the bus is the kernel's from boot. No register is written until
the first start.
"""


def attach(log=print):
    """Say whether this image plays sound, and take the codec's bus address.
    Returns the module or None."""
    try:
        import moy_audio
    except ImportError:
        log("audio: none in this image")
        return None
    if not moy_audio.attach():
        log("audio: the I2C bus refused the codec")
    return moy_audio

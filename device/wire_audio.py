"""Audio's provider: the console's audio backend factory, for the board whose
image plays sound (docs/kernel_survival_2026-10.md section 2 item 1).

`audio_factory()` is what a board hands `build_desktop` as `make_audio`: the
device tier's `device_audio.make_audio` over the native `moy_audio`, or None
where the image has no backend, in which case a cart plays into
`audio_session._SilentAudio`.
"""


def audio_factory():
    """The audio backend factory, or None on an image without one."""
    try:
        from device_audio import make_audio
    except ImportError:
        return None
    return make_audio

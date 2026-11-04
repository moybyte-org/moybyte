"""`moybyte.mic`: the `mic` device API's face (docs/os_2026-11/hardware.md
sections 1 and 4) over its driver (native/moy_audio/moy_mic.h, the module
moy_mic). Needs the `mic` permission; one process holds the microphone at a
time.

  read(buf) -> n     PCM16 mono samples at rate() into `buf` (an array('h')
                     or a bytearray), oldest first, the microphone taken for
                     the caller at its first read; allocates nothing
  open() -> rate     take it now (OSError EBUSY while another process holds
                     it); a second open by its holder is nothing
  close()            let it go
  rate()             the samples a second
  drops()            samples lost because the holder read too late
  holder()           the pid holding it, 0 for none: the shell's recording
                     indicator reads it

The holder's end (exit, crash, kill, a run's end) lets it go through
`moybyte.ENDS`. The host has a microphone only under its stand-in
(runtime/host/mic_sim.py)."""

try:
    import moy_mic as _N
except ImportError:  # host, or a console whose image has no audio
    _N = None
if _N is None:
    try:
        from runtime import moy_mic as _N
    except ImportError:  # a console whose image has no audio: no microphone
        _N = None

try:
    import devices as _D
except ImportError:  # host: the runtime package
    from runtime import devices as _D


def open():  # noqa: A001 -- the verb's name
    p = _D.need("mic")
    _D.check(_N.open(p))
    return _N.rate()


def read(buf):
    p = _D.need("mic")
    if _N.owner() != p:
        _D.check(_N.open(p))
    return _D.check(_N.read(p, buf))


def close():
    _N.close(_D.pid())


def rate():
    return _N.rate() or None


def drops():
    return _N.drops()


def holder():
    return _N.owner()


def present():
    return _N is not None and bool(_N.present())


def sweep(pid):
    return _N.sweep(pid)

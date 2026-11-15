"""The frame's state: whether the next frame must paint (`dirty`, which
`Workstation.mark_dirty` sets) and the timing fields the frame loop writes
every frame, on one plain object (`ws.frame_state`).
"""


class FrameState:
    """The fields the frame loop writes every frame, on a plain object the
    Workstation holds as `frame_state` and never rebinds. A store on a
    class with base classes looks through all of them for a property setter
    (about 57 us on a T-Deck when the Workstation had ten); a store here
    costs under 2. A base class or a property would bring that walk back;
    `__slots__` changes nothing on MicroPython."""

    def __init__(self):
        # "Redraw this frame": True at the start so the first frame paints,
        # set by `Workstation.mark_dirty` whenever a visible change could
        # have happened, cleared by the frame's gate.
        self.dirty = True
        self.dirty_in = True        # a change reported before this input (handle_input)
        self.frames_drawn = 0       # frames that drew and flushed (test witness)
        self.since_draw = 0.0       # seconds since the last drawn frame
        self.fps = 0.0              # smoothed frames/sec DRAWN (EMA, #217)
        self.frame_dt_ms = 33.0     # last loop tick in ms (kinetic velocity, #113)
        self.pointer_dt_ms = 33.0   # ...as charged to the pointer SAMPLE
                                    # (_tick_pointer_dt), None on a repeat
        self.stale_ptr_ms = 0.0     # the repeats' time, banked for the next sample

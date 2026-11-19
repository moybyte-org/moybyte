# A careless save: pmem written every frame, under a tone that never stops.
# Cell 0 is the frame count, so it changes every frame; cell 1 counts the
# runs. The fixture the pmem save queue is measured with: a save must never
# stall a frame or starve the audio (runtime/run/pmem_queue.py).

n = [0]


def _init():
    pmem(1, pmem(1) + 1)


def _update(dt):
    n[0] += 1
    pmem(0, n[0])
    if n[0] % 20 == 1:
        beep(220, 1.0)


def _draw():
    cls(1)
    print("pmem 0 = %d" % pmem(0), 8, 8, 7)
    print("runs %d" % pmem(1), 8, 20, 7)

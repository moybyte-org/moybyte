"""Minimal input state for the v0.4 userland: edge-detected buttons + pointer.

Mirrors the firmware `moybyte` input contract (held / pressed / released) so
cartridges poll the same way on host and device.
"""


class InputState:
    BUTTONS = ("left", "right", "up", "down", "a", "b", "run", "home")

    def __init__(self):
        self._held = set()
        self._prev = set()
        self.pointer = None  # (x, y) or None
        self.last_key = 0    # last typed ASCII byte (for the shared code editor)

    def set_held(self, name, down):
        if down:
            self._held.add(name)
        else:
            self._held.discard(name)

    def begin_frame(self):
        # Snapshot for edge detection; call once per frame before polling.
        self._pressed = self._held - self._prev
        self._released = self._prev - self._held
        self._prev = set(self._held)

    _mask_order = None      # the tuple _mask_bit was built from (identity key)
    _mask_bit = None

    def button_masks(self, order):
        """(held, pressed) as bitmasks over `order`, in ONE call.

        Exists because moycore's per-frame snapshot needs exactly these two
        integers and was building them with sixteen `held`/`pressed` calls --
        ~100us of pure call overhead on the S3, every frame, in the glue whose
        entire job is to stop the cart making calls like that. Walks the held
        set (usually 0-2 entries) rather than the order, so the common case is
        a couple of dict lookups.

        `order` IS REQUIRED, and that is the fix for a bug rather than an API
        preference. This method used to bit-pack in `BUTTONS` order, which read
        as tidy single-sourcing and was wrong: the boards run a DIFFERENT
        InputState whose BUTTONS is a different tuple in a different order, so
        the same call returned different bits on host and device and every Lua
        cart on both boards played with its d-pad rotated. The consumer names
        the order it wants (moycore wants lua_ext.MOY_BUTTONS -- libmoy's ABI),
        and a caller that forgets now gets a TypeError instead of wrong bits.
        """
        if self._mask_order is not order:
            self._mask_order = order
            self._mask_bit = {n: 1 << i for i, n in enumerate(order)}
        h = p = 0
        bit = self._mask_bit
        for n in self._held:
            h |= bit.get(n, 0)
        for n in getattr(self, "_pressed", ()):
            p |= bit.get(n, 0)
        return h, p

    def held(self, name):
        return name in self._held

    def pressed(self, name):
        return name in getattr(self, "_pressed", set())

    def released(self, name):
        return name in getattr(self, "_released", set())

    def release_all(self):
        self._held.clear()

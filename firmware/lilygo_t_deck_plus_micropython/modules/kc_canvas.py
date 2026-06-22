# KidCode native canvas blitter.
#
# Bypasses LVGL's software-rotated lv.canvas flush (the ~13ms/frame CPU-bound
# "pump" cost measured on the T-Deck) by blitting the 128x128 RGB565 framebuffer
# straight to the ST7789 over the lcd_bus DMA path. This is pure Python: it drives
# the already-exposed lcd_bus.SPIBus API (allocate_framebuffer / tx_param /
# tx_color) and skips LVGL's lv_draw_sw_rotate entirely, leaving the only real
# per-frame cost the SPI DMA transfer itself (bus-bound, not CPU-bound).
#
# On the host (no lcd_bus / no hardware) `make_blitter` returns None, so the
# renderer keeps the LVGL path and the simulator/tests are unaffected.

CASET = 0x2A  # column address set
RASET = 0x2B  # row address set
RAMWR = 0x2C  # memory write


class Blitter:
    """Blits a 128x128 RGB565 source buffer to the panel via DMA.

    The source buffer is the framebuf.FrameBuffer backing store (native LE byte
    order). flush() copies it into a private DMA-capable buffer, sets the write
    window, and DMAs the bytes. lcd_bus.tx_color byte-swaps its data argument in
    place, so that swap is applied to the private DMA buffer only -- never to the
    live framebuf backing store (which framebuf rewrites in native order).
    """

    def __init__(self, bus, canvas_x, canvas_y, canvas_size):
        import kc_alloc

        self._bus = bus
        self._size = canvas_size
        x1 = canvas_x
        x2 = canvas_x + canvas_size - 1
        y1 = canvas_y
        y2 = canvas_y + canvas_size - 1
        # ST7789 address params are big-endian 16-bit per coordinate.
        self._caset = bytes([(x1 >> 8) & 0xFF, x1 & 0xFF, (x2 >> 8) & 0xFF, x2 & 0xFF])
        self._raset = bytes([(y1 >> 8) & 0xFF, y1 & 0xFF, (y2 >> 8) & 0xFF, y2 & 0xFF])
        self._win = (x1, y1, x2, y2)
        # DMA-capable internal-SRAM transfer buffer (32KB for 128x128 RGB565).
        # Allocated via kc_alloc, not lcd_bus.allocate_framebuffer, because the
        # latter is capped at 2 slots (both taken by the LVGL draw buffers).
        self._dma = kc_alloc.malloc_dma(canvas_size * canvas_size * 2)
        self._source = None

    def set_source(self, buf):
        self._source = buf

    def flush(self):
        bus = self._bus
        # Copy the framebuf backing store into the DMA buffer. memoryview slice
        # assignment is a C-level memcpy (~0.1ms for 32KB). tx_color then
        # byte-swaps this buffer in place and busy-waits for the SPI transfer.
        self._dma[:] = self._source
        bus.tx_param(CASET, self._caset)
        bus.tx_param(RASET, self._raset)
        x1, y1, x2, y2 = self._win
        bus.tx_color(RAMWR, self._dma, x1, y1, x2, y2, 0, True)


def make_blitter(bus, canvas_x, canvas_y, canvas_size):
    """Return a native Blitter, or None when lcd_bus/hardware is unavailable."""
    if bus is None:
        return None
    try:
        return Blitter(bus, canvas_x, canvas_y, canvas_size)
    except Exception as exc:
        print("KidCode kc_canvas disabled:", exc)
        return None

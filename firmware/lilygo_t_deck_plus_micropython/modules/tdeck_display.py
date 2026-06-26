_spi_bus = None
_display_bus = None
_display = None
_backlight_pin = None
_backlight_gpio = None


def init_display():
    global _spi_bus
    global _display_bus
    global _display
    global _backlight_pin
    global _backlight_gpio
    from machine import Pin
    import lcd_bus
    import machine
    import st7789
    import lvgl as lv
    import task_handler
    from tdeck_board import init_board_pins

    init_board_pins()
    print("KidCode display board pins ready")

    # The T-Deck ST7789 is wired in portrait-native geometry. LVGL rotates it
    # to the landscape 320x240 shell after panel init.
    width = 240
    height = 320
    backlight = 42
    dc = 11
    mosi = 41
    miso = 38
    sck = 40
    host = 1
    cs = 12
    freq = 80000000   # PERF (#33): 40->80 MHz halves the ~31ms full-frame SPI flush.
    # If the panel shows tearing/corruption/garbage, it's over the ST7789's limit on
    # this wiring -> drop to 62500000, then 40000000. The flush is bandwidth-bound, so
    # this is the main flush lever (band count barely matters -- same total bytes).

    print("KidCode display SPI starting")
    _spi_bus = machine.SPI.Bus(host=host, mosi=mosi, miso=miso, sck=sck)
    display_bus = lcd_bus.SPIBus(spi_bus=_spi_bus, freq=freq, dc=dc, cs=cs)
    _display_bus = display_bus

    print("KidCode display LVGL starting")
    lv.init()
    display = st7789.ST7789(
        data_bus=display_bus,
        display_width=width,
        display_height=height,
        backlight_pin=backlight,
        color_space=lv.COLOR_FORMAT.RGB565,
        color_byte_order=st7789.BYTE_ORDER_BGR,
        rgb565_byte_swap=True,
    )
    display._ORIENTATION_TABLE = (0, 160, 192, 96)
    try:
        display.set_power(True)
    except AttributeError:
        pass
    print("KidCode display panel init")
    display.init()
    try:
        display.set_rotation(lv.DISPLAY_ROTATION._270)
    except AttributeError:
        display.set_rotation(3)
    # Backlight is deliberately LEFT OFF here (#45): the panel is now init'd but its
    # GRAM still holds power-on noise, so lighting it would show the boot "CRT" flash.
    # set_backlight(True) is called from the boot path AFTER the first composed frame
    # is flushed (kid_runtime.run_desktop) so the user only ever sees the real desktop.
    # Prefer the ST7789 driver's own set_backlight (it owns GPIO `backlight`); only
    # fall back to a raw Pin if the driver lacks it -- creating a competing Pin on a
    # driver-owned GPIO is exactly the kind of dual-ownership the bus notes warn about.
    _display = display
    _backlight_gpio = backlight
    try:
        display.set_backlight(0)
    except AttributeError:
        _backlight_pin = Pin(backlight, Pin.OUT, value=0)

    print("KidCode display ready (backlight off until first frame)")
    handler = task_handler.TaskHandler(duration=5)
    return lv, display, handler


def set_backlight(on=True):
    """Turn the panel backlight on/off after init.

    Kept off through init+prefetch so the ST7789's power-on GRAM garbage never
    shows (#45); the boot path calls this once the first KidCode frame has been
    composited and flushed. Uses the LVGL display's set_backlight when available
    (PWM duty, driver-owned GPIO); only falls back to the raw backlight GPIO if the
    driver has no set_backlight (matches the original init fallback)."""
    global _backlight_pin
    from machine import Pin
    duty = 100 if on else 0
    if _display is not None:
        try:
            _display.set_backlight(duty)
            return
        except AttributeError:
            pass
    if _backlight_pin is None and _backlight_gpio is not None:
        _backlight_pin = Pin(_backlight_gpio, Pin.OUT)
    if _backlight_pin is not None:
        _backlight_pin.value(1 if on else 0)


def get_spi_bus():
    return _spi_bus


def get_display_bus():
    # The lcd_bus.SPIBus exposes the native DMA blitter API (allocate_framebuffer,
    # tx_param, tx_color) that kc_canvas drives to bypass LVGL's flush.
    return _display_bus

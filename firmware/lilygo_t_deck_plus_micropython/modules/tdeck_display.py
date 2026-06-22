_spi_bus = None
_display_bus = None


def init_display():
    global _spi_bus
    global _display_bus
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
    freq = 40000000

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
    try:
        display.set_backlight(100)
    except AttributeError:
        Pin(backlight, Pin.OUT, value=1)

    print("KidCode display ready")
    handler = task_handler.TaskHandler(duration=5)
    return lv, display, handler


def get_spi_bus():
    return _spi_bus


def get_display_bus():
    # The lcd_bus.SPIBus exposes the native DMA blitter API (allocate_framebuffer,
    # tx_param, tx_color) that kc_canvas drives to bypass LVGL's flush.
    return _display_bus

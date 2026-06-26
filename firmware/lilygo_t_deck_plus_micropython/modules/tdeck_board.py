POWERON = 10
SDCARD_CS = 39
RADIO_CS = 9
TFT_CS = 12
SPI_MISO = 38
BACKLIGHT = 42


def init_board_pins():
    from machine import Pin

    Pin(POWERON, Pin.OUT, value=1)
    Pin(SDCARD_CS, Pin.OUT, value=1)
    Pin(RADIO_CS, Pin.OUT, value=1)
    Pin(TFT_CS, Pin.OUT, value=1)
    Pin(SPI_MISO, Pin.IN, Pin.PULL_UP)
    # Backlight stays OFF through panel init + cart prefetch so the ST7789's
    # power-on GRAM noise (the boot "CRT" flash, #45) is never lit. The boot
    # path turns it on only after the first KidCode frame is composed+flushed
    # (tdeck_display.set_backlight, called from kid_runtime.run_desktop).
    Pin(BACKLIGHT, Pin.OUT, value=0)

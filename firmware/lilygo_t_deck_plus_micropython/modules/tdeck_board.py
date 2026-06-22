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
    Pin(BACKLIGHT, Pin.OUT, value=1)

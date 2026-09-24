idf_component_register(SRCS "main.c" "lcd.c" "input.c" INCLUDE_DIRS "."
    REQUIRES wamr esp_timer esp_partition esp_lcd esp_driver_spi esp_driver_gpio
             esp_driver_i2c pthread)

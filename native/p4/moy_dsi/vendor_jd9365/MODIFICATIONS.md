# Provenance of `esp_lcd_jd9365` (Guition JC8012P4A1C panel driver)

Two files, `esp_lcd_jd9365.c` and `include/esp_lcd_jd9365.h`, copied
byte-for-byte on 2026-09-06 from the factory demo sources Guition publishes
for the JC8012P4A1C_I_W_Y (`1-Demo/arduino-examples/esp32p4_lvgl_v8/src/lcd/`
in the vendor's zip, mirrored at
https://github.com/DevinWatson/10.1-inch-ESP32P4-Xiaozhi-ESP32-C6-JC8012P4A1C_I_W_Y).

Both carry Espressif's SPDX header (`SPDX-License-Identifier: Apache-2.0`,
2024 Espressif Systems): they are Espressif's `esp_lcd_jd9365` component
(esp-iot-solution, `components/display/lcd/esp_lcd_jd9365`) with the vendor's
own changes -- the panel's initialization table (`vendor_specific_init_default`,
the HKC QP101BS01-1 800x1280 sequence), a 1500 Mbps 2-lane bus config, and the
800x1280 60 Hz DPI timing macro. Guition ships no NOTICE and no change
statement; this file is Moybyte's record of where the bytes came from.

**Moybyte has not modified either file.** The board bring-up that *uses* the
driver (`modmoy_dsi.c`, one directory up) is Moybyte's own code under the
repository licence; it overrides `num_fbs` and `use_dma2d` at runtime rather
than editing the vendor macros.

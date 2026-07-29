# Moybyte modifications to `esp_lcd_ek79007`

Upstream: **`espressif/esp_lcd_ek79007` v2.0.2~1**, from the ESP Component
Registry (https://components.espressif.com/components/espressif/esp_lcd_ek79007),
whose source is `components/display/lcd/esp_lcd_ek79007` in
https://github.com/espressif/esp-iot-solution at commit
`12f6ca1182ec48889b17ec570fadaaf267cb336e` (recorded in `idf_component.yml`).

Licence: **Apache-2.0** — see `license.txt`. Upstream ships no `NOTICE` file.

## Change statement (Apache-2.0 §4(b))

**Moybyte has not modified any file in this directory.** Every file is
byte-for-byte identical to the upstream component:

    CHANGELOG.md  CMakeLists.txt  README.md  license.txt
    esp_lcd_ek79007.c  include/esp_lcd_ek79007.h
    test_apps/**

(`idf_component.yml` matches the registry-packaged form of the manifest, which
the registry rewrites at publish time — it carries `repository_info.commit_sha`
and drops the `sbom` block relative to the git tree. It is upstream's file
either way; Moybyte did not edit it.)

Because nothing was changed, Apache-2.0 §4(b)'s "carry prominent notices
stating that You changed the files" obligation is not triggered. This file
records the determination so a reviewer does not have to re-derive it.

## Moybyte's own code is OUTSIDE this directory

The board bring-up that *uses* this driver — panel/DSI/DPI setup, the
triple-framebuffer scan-out, the underrun hook — is Moybyte's own work in
`../modmoy_dsi.c` and `../micropython.cmake`, under this repository's licence.
`micropython.cmake` compiles `vendor/esp_lcd_ek79007.c` directly (bypassing
`vendor/CMakeLists.txt`) and hand-defines the `ESP_LCD_EK79007_VER_*` macros
that the component's own CMake would otherwise supply; that is a build-wiring
choice in Moybyte's file, not an edit to upstream's.

## Verifying

```bash
B=https://raw.githubusercontent.com/espressif/esp-iot-solution/12f6ca1182ec48889b17ec570fadaaf267cb336e/components/display/lcd/esp_lcd_ek79007
for f in esp_lcd_ek79007.c include/esp_lcd_ek79007.h license.txt \
         CMakeLists.txt README.md CHANGELOG.md; do
  curl -sS "$B/$f" | diff -q - "$f"
done
```

# MoyByte hardware lineup

Roadmap snapshot of the planned **MoyByte** hardware family. Moybyte v0.4 is a host==device
shared console (`runtime/` → frozen onto each device), so a device is just a **backend**
(display / input / SD / transport) under one shared UI + cart format. The cart contract —
**320×240 indexed MOY64 canvas** — is identical everywhere, so *one cart runs on every
tier*. Adding a tier is a backend port, not a fork.

Status: only the **Player-class** device works today (LilyGO T-Deck). This doc is the
direction; issues track the work.

## The three tiers

| Tier | Chip | Display | Input | Carts / storage | Role | Status |
|---|---|---|---|---|---|---|
| **MoyByte Zero** | ESP32-S3 (WROOM-1 N16R8) | none → **browser** | browser (web view) | flash, over WiFi | cheapest, phone-played companion | planned (enabled by #41) |
| **MoyByte Player** | ESP32-S3 (WROOM-1U N16R8) | 2.4″ IPS 320×240 | **D-pad + A/B + Start/Select** | **microSD ("cartridge")** | pocket game console | dev today on T-Deck; gamepad device picked |
| **MoyByte One** | ESP32-P4 (+ C6) | 7″ MIPI-DSI 1024×600 | USB-HID kbd → integrated | SDIO microSD | large-screen workstation | porting (#58) — **first bespoke product** |

**Naming:** *Zero* and *One* are the two bits in a **Byte** (0/1) — Zero = the minimal
headless tier (cf. Pi Zero; there's literally an ESP32-S3-Zero board), One = the flagship.
*Player* = the game console in the middle. A cart runs on all three.

## MoyByte Zero — the wireless mini
- **Headless, played in a browser** over the device's WiFi. Key idea: with the web-view
  draw-command protocol (#41), the device **doesn't rasterize at all** — it runs cart
  *logic* + streams draw commands, and **the browser is the GPU**. No framebuffer/flush on
  device → tiny RAM/CPU, cart runs fast.
- **Cardless.** Carts live in the flash filesystem, pushed/managed **over WiFi from the
  browser** (a future "upload/list carts" page in the web UI). SD is *not* the Zero's thing.
- **Board:** off-the-shelf S3. Prefer **16 MB / N16R8** (WROOM-1 N16R8 = 16 MB flash + 8 MB
  PSRAM) for app + OTA + a cart library *and* module-parity with the Player. Candidates:
  - **WeAct ESP32-S3 N16R8** (~$6, small, BOOT button + WS2812 RGB LED) — top dev pick.
  - **ESP32-S3-DevKitC-1-N16R8** (reference; button + RGB LED; everywhere).
  - **M5 AtomS3R** (8 MB flash + 8 MB PSRAM + a 0.85″ status screen to show the URL/QR) —
    if a status screen beats the 16 MB headroom.
  - Tiny-but-small-flash (≤8 MB): XIAO ESP32-S3, Waveshare ESP32-S3-Zero, Lolin S3 Mini.

## MoyByte Player — the game console
- **Device picked:** an off-the-shelf **"Retro-Go ESP32-S3 Handheld"** (AliExpress, ~€34).
  Confirmed specs: **ESP32-S3-WROOM-1U N16R8 (16 MB flash + 8 MB PSRAM)**, **2.4″ IPS,
  240×320 = native 320×240** (ILI9341-class, no scaling), **D-pad + A/B + START/SELECT/
  MENU/OPTION** (maps 1:1 to `btn()`), **microSD**, I2S speaker, battery, fully
  **open-source/DIY** (flashable). It's the off-the-shelf S3 gamepad handheld with a native
  canvas — better for *playing* than the T-Deck's keyboard/trackball.
- **SD = the cartridge.** Swap the microSD to swap game libraries — fits the console identity.
- **Port:** the S3 backend we already ship + a new `BOARD_CONFIG` in `build.sh` (ILI9341
  `DISPLAY=`, GPIO button reader replacing the I2C keyboard/trackball, SD + I2S pins).
  `moy_gfx`/compositor are panel-agnostic. Flush ceiling ~45–50 fps (SPI) — expected for this
  tier; the web view / One cover higher perf. (Player-port issue: TBD, parallel to #58.)
- The **LilyGO T-Deck** stays a Player-class *keyboard* variant (good for on-device typing);
  the Retro-Go handheld is the *gamepad* variant.

## MoyByte One — the workstation (first shippable product)
- **Device:** ESP32-P4-WIFI6-Touch-LCD-7B (bespoke; the first product we design + ship).
  7″ 1024×600 MIPI-DSI, GT911 touch, ESP32-C6 Wi-Fi, **32 MB PSRAM**, USB-OTG-HS (host),
  SDIO microSD, battery.
- **Keyboard:** external **USB-HID** via the OTG-HS host port now → integrated later. A
  USB-HID keyboard sends real make/break events (hold-to-move free, clean ASCII), so it's
  *simpler* than the T-Deck's ESP32-C3 raw-matrix hack.
- **Why it's the perf escape:** MIPI-DSI runs continuous scanout from a PSRAM framebuffer →
  **no flush ceiling**; SD is on SDIO (separate bus) → the #56 SD↔display war is gone; 32 MB
  PSRAM → the #38/#40 Wi-Fi-vs-LCD RAM squeeze eases.
- **Build path:** lvgl_micropython has no P4/DSI support → move to **mainline MicroPython
  v1.28 `ESP32_GENERIC_P4` (C6_WIFI)** + a native MIPI-DSI panel C module. Low-loss (we
  barely use LVGL). See #58.
- **"Desktop look":** carts stay 320×240 (upscaled to the panel); the *shell* can render at
  native hi-res for a workstation feel (the TIC-80 split). Follow-up, not the base port.

## Module-parity (the modular angle)
Zero (**WROOM-1 N16R8**) and Player (**WROOM-1U N16R8**, "U" = external antenna) are the
**same S3 module** → one firmware backend, one SKU. This makes a modular **"Zero pops out
and slots into a Player carrier"** product (one brain, two shells) feasible — the carrier
just needs to break out enough pins for the screen + D-pad + SD + I2S.

## Open questions
- Branding: MoyByte (devices) / MoyChip (?).
- Zero: pure browser companion, or pair with a cheap clip-on screen?
- Modular Zero-into-Player carrier vs. two separate off-the-shelf devices.
- BOM / price target per tier.

## Tracking
- **#58** — MoyByte One / ESP32-P4 port (the first product).
- **#59** — this lineup (umbrella).
- **#41 / #22** — device web view (the Zero's enabler) + the payload diet / 30 fps stream mode.
- **#43** — the Player's SPI flush ceiling (~45–50 fps).
- **#53** — OTA across tiers. **#56** — SD↔display (gone on the One). **#57** — map big-sprite brush.
- Player-port issue (Retro-Go S3 handheld board config): *to be created*.

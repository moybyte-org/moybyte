# Moybyte BOM & Retail Pricing Analysis

**Date:** 2026-07-01
**Scope:** per-tier component BOM + realistic retail estimate for Zero / Player / One (see `moybyte_Console_Plan_v0_5.md` §3).
**Status:** reference. Grounds the §15 "per-tier price" open decision. Prices are 2026 estimates — re-verify before committing a build.

## Assumptions & method
- **Volume:** all BOM figures at **~1,000-unit** build (a small first run). Costs fall materially at 10k+ — noted where it matters.
- **BOM scope:** landed component cost + bare PCB + SMT/assembly + enclosure + packaging. **Excludes** one-time tooling (called out separately for the One), certification (FCC/CE ≈ $5–15k one-time), NRE/engineering, shipping/duties, software, warranty/returns.
- **Margin model:** retail ≈ **2–2.5× BOM** direct-to-consumer (DTC); ≈ **3× BOM** through a distributor/retail channel (standard CE keystone).
- Cited prices are retail / small-qty dev-board or distributor prices; the "@~1k" column applies a volume discount and is flagged as estimate where no bulk cite exists.
- Currency: GBP→USD ~1.27, EUR→USD ~1.08.

---

## Tier 1 — Zero (headless companion / dev board)

Buy the XIAO board, don't build the module from bare silicon.

| Component | Est. @~1k | Source | Notes |
|---|---|---|---|
| Seeed XIAO ESP32-S3 Plus (16MB/8MB) | $5.75 | [seeedstudio](https://www.seeedstudio.com/Seeed-Studio-XIAO-ESP32S3-Plus-p-6361.html) ($7.90 retail, $6.50 @10+) | ~$5.5–6 at 1k contract |
| USB-C cable (short, bulk) | $0.60 | est. | |
| Injection-molded shell (2-part) | $0.80 | est. simple ABS | Tooling ~$3–8k separate |
| Packaging + label | $0.50 | est. | |
| **BOM subtotal** | **≈ $7.65** | | |

**Retail:** DTC 2–2.5× = **$15–19** · channel 3× = **$23**
**Optional M5AtomS3R variant** (tiny status screen): board $17.50 → BOM ≈ $19.5 → DTC 2× ≈ **$39**.
**Verdict vs ~$10:** **impossible at healthy margin.** $10 ≈ near-cost DTC loss-leader only; impossible via retail channel.

---

## Tier 2 — Player (pocket cart console)

| Component | Est. @~1k | Source | Notes |
|---|---|---|---|
| ESP32-S3-WROOM-1-N16R8 | $3.50 | [JLCPCB/LCSC](https://jlcpcb.com/partdetail/3198300-ESP32_S3_WROOM_1N16R8/C2913202) ($3.35–5.12 @1.3k) | |
| 2.4" IPS SPI LCD 320×240 (ST7789) | $4.00 | [buydisplay](https://www.buydisplay.com/2-4-inch-ips-240x320-tft-lcd-display-capacitive-touch-screen) (~$9.66 retail) | bulk est.; no touch |
| D-pad + A/B + Start/Select + silicone pad | $0.80 | est. | ~8 switches |
| microSD push-push slot | $0.25 | est. | |
| MAX98357A I2S amp + speaker | $1.70 | [LCSC C910544](https://www.lcsc.com/product-detail/C910544.html) | IC ~$1.2 + spkr $0.5 |
| LiPo ~1500mAh + TP4056 + protection | $3.50 | [TP4056](https://www.addicore.com/products/tp4056-tc4056a-lithium-battery-charger-and-protection-module); cells ~$0.93+ [alibaba](https://www.alibaba.com/showroom/lipo-battery-cell-5000mah.html) | battery ~$2.5 |
| Custom 4-layer PCB (~50×80mm) | $1.50 | est. [JLCPCB](https://jlcpcb.com) | |
| Passives, USB-C, regulator, connectors | $2.00 | est. | |
| Injection-molded enclosure (2–3 part) | $2.50 | est. | Tooling ~$8–20k separate |
| Assembly (SMT + final, @1k) | $4.00 | est. | |
| Packaging | $1.00 | est. | |
| **BOM subtotal** | **≈ $24.75** | | |

**Retail:** DTC 2–2.5× = **$50–62** · channel 3× = **$74**
**Anchor:** AliExpress "Retro-Go ESP32-S3" handheld ≈ **€34 (~$37)** [aliexpress](https://www.aliexpress.com/item/1005012108147409.html) · assembled [$49.99 Amazon](https://www.amazon.com/Handheld-Portable-ESP32-S3FN8-Compatible-Nintendo/dp/B0DC6LD91L). A €34 finished unit ⇒ white-label BOM ~$15–18 at their volume + thin margin.
**Verdict vs $35–50:** **tight but realistic DTC-only.** 2× DTC = $50 tops the band; the $35 floor needs BOM ~$18–20 (smaller battery, bare-PCB display, higher volume — what the €34 anchor does). 3× retail ($74) impossible.

---

## Tier 3 — One (7" make-workstation)

**Note:** ESP32-P4NRW32 includes 32MB PSRAM in-package — no separate PSRAM line.

| Component | Est. @~1k | Source | Notes |
|---|---|---|---|
| ESP32-P4NRW32 (incl. 32MB PSRAM) | $4.00 | [LCSC C22387510](https://www.lcsc.com/product-detail/C22387510.html) ($4.47 @1) | |
| ESP32-C6-MINI-1 (Wi-Fi6) | $2.00 | [LCSC C5736265](https://www.lcsc.com/product-detail/C5736265.html) ($2.64 @1) | |
| 7" MIPI-DSI 1024×600 IPS + GT911 touch | $22.00 | [alibaba](https://www.alibaba.com/product-detail/7-inch-GT911-capacitive-touch-1024_1600974091696.html) (~$40 @1pc) | biggest line; bulk est. |
| SDIO microSD slot | $0.30 | est. | |
| Keyboard **A:** cheap USB-HID | $4.00 | [alibaba](https://www.alibaba.com/showroom/usb-mini-keyboard.html) (~$3.65 @100) | |
| Keyboard **B:** custom membrane/scissor kid kbd | $6–9 | est. [made-in-china](https://www.made-in-china.com/products-search/hot-china-products/Keyboard_Price.html) | +key tooling ~$5–15k |
| LiPo ~4000mAh + charge/PMIC + protection | $6.00 | [alibaba](https://www.alibaba.com/showroom/lipo-battery-cell-5000mah.html) | battery ~$4.5 |
| Speaker + amp | $2.00 | [LCSC C910544](https://www.lcsc.com/product-detail/C910544.html) | |
| Custom PCB (larger, 4–6 layer) | $4.00 | est. | |
| Passives, USB-C, power path, connectors | $4.00 | est. | |
| Injection-molded enclosure (7" bezel + back) | $6.00 | est. | tooling separate ↓ |
| Assembly (larger board + display bond + final) | $8.00 | est. | |
| Packaging | $2.00 | est. | |
| **BOM subtotal (USB-HID kbd)** | **≈ $64.30** | | |
| **BOM subtotal (custom kid kbd)** | **≈ $67–70** | | |

**One-time tooling capex (separate):** steel injection-mold ≈ **$15k–40k per part** (bezel + back = 2 parts). Over 1,000 units = **$30–80/unit**; amortizes to ~$3–8/unit only at **10k+ units**. Custom keyboard adds ~$5–15k. **The One is not economically sane at 1k volume.**

**Retail (ex-tooling, USB-HID):** DTC 2–2.5× = **$129–161** · channel 3× = **$193**
**Anchors:** bare Waveshare **ESP32-P4-WIFI6-Touch-LCD-7B** (7" 1024×600, GT911, C6, 32MB PSRAM, SDIO, battery; no enclosure/kbd) retails **$79.99–89.99** [CNX Jan 2026](https://www.cnx-software.com/2026/01/17/tablet-like-esp32-p4-based-7-8-and-10-1-inch-hmi-displays-integrate-wi-fi-6-connectivity-5mp-camera/) · [waveshare](https://www.waveshare.com/esp32-p4-wifi6-touch-lcd-7b.htm). Guition JC1060P470C 7" P4 board ≈ **$33–37** [CNX](https://www.cnx-software.com/2025/07/18/14-development-board-features-guition-esp32-p4-esp32-c6-module/).
**Verdict vs $100–150:** **realistic only at the top, DTC-only, ignoring tooling.** 2× DTC = $128–140 in-band; 2.5× exceeds it; 3× retail ($193) impossible. $100 floor needs BOM ~$40–50 (cheaper panel at volume, smaller battery, Guition board as the electronics core). Tooling makes $100–150 impossible below 10k units.

---

## Sanity-check: comparable shipping products

| Product | Retail | Source | Note |
|---|---|---|---|
| Retro-Go ESP32-S3 handheld | ~€34/$37 (assembled $49.99) | [aliexpress](https://www.aliexpress.com/item/1005012108147409.html) · [amazon](https://www.amazon.com/Handheld-Portable-ESP32-S3FN8-Compatible-Nintendo/dp/B0DC6LD91L) | direct Player anchor — $35 buildable at scale |
| M5Cardputer (Adv) | £32.30/~$41 | [thepihut](https://thepihut.com/products/m5stack-cardputer-adv) | S3 + screen + kbd; near Player band |
| BBC micro:bit v2 | $17.95 | [adafruit](https://www.adafruit.com/product/4781) | high-volume, education-subsidized — floor ref |
| LilyGO T-Deck Plus | $70.99 | [lilygo](https://lilygo.cc/en-us/products/t-deck-plus-1) | S3 + 2.8" + kbd + LoRa/GPS; closest "One-lite" |
| Waveshare ESP32-P4 7" (7B) | $79.99–89.99 | [cnx](https://www.cnx-software.com/2026/01/17/tablet-like-esp32-p4-based-7-8-and-10-1-inch-hmi-displays-integrate-wi-fi-6-connectivity-5mp-camera/) | bare One electronics anchor |
| Playdate (Panic) | $229 | [engadget](https://www.engadget.com/gaming/the-diminutive-playdate-console-is-getting-a-price-increase-to-229-on-march-25-120004199.html) · [wikipedia](https://en.wikipedia.org/wiki/Playdate_(console)) | bespoke case + custom input + retail margin — fully-productized reference |

---

## Bottom line

| Tier | BOM (~1k) | DTC 2–2.5× | Retail 3× | Target | Verdict |
|---|---|---|---|---|---|
| **Zero** | ~$7.6 | $15–19 | $23 | ~$10 | impossible at margin — $10 ≈ cost |
| **Player** | ~$25 | $50–62 | $74 | $35–50 | tight, DTC-only; need ~$18–20 BOM for floor |
| **One** | ~$64 +tooling | $129–161 | $193 | $100–150 | top-of-band, DTC-only, ignoring tooling; needs 10k+ |

**Two cross-cutting truths:**
1. **Retail-channel (3×) pricing is impossible for every tier — the bands only work DTC.**
2. **Volume is the lever.** Targets appear at 10k+ units; the One's tooling breaks the band below that. Launch premium → fund volume → price down.

The two soft anchors (€34 Retro-Go, $79 Waveshare P4) undercut these BOMs because they're higher-volume white-label boards with no enclosure/battery/keyboard build-out and thin margins — add a kid-proof case, battery, and keyboard at 1k volume and you land where these estimates sit.

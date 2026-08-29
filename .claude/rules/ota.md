---
paths:
  - "device/moy_ota.py"
  - "device/moy_c6_update.py"
  - "runtime/update_ui.py"
  - "tools/ota_*.py"
  - "tools/release.py"
  - "tools/publish_firmware_release.py"
---

<!-- Firmware updates: install machine, channels, signature policy. -->

- **OTA and firmware updates (#53)** — verified end to end on the T-Deck and the
  P4 (2026-08-02: real WiFi, on-device signature check, streamed install, boot
  into the new slot, rollback self-heal). **Timings, sizes and rates live in
  #53**, not here.
  - **Every board is dual-OTA** (`otadata + ota_0 + ota_1 + vfs`), so the
    one-time migration is HISTORY. The slot sizes differ per board and are NOT
    repeated here: each board's partition CSV is the only thing that decides
    whether an image fits, and the build prints the headroom against it (#168).
    **The Zero's is the one authored under a real constraint** — 8MB of flash,
    where the console layout does not fit and the bootloader REJECTS the table
    into a silent boot loop. Its CSV carries the arithmetic and the measured
    image it is sized against.
  - **The OTA payload is the APP-PARTITION image, never the merged one.**
    `…_app.bin` is the payload; `…​.bin` is bootloader+table+app for a cable flash.
    Handing the merged one to `esp32.Partition` writes a bootloader into an app
    slot.
  - **`step()` returns True WHILE MORE REMAINS** (`update_ui` drives it as
    `more = u.step()`). Inverting it writes a truncated image, whose `set_boot` is
    then correctly refused with `ESP_ERR_OTA_VALIDATE_FAILED`.
  - **A cable flash must erase otadata FIRST**, or a board that has taken an OTA
    writes ota_0 and boots the stale ota_1 — indistinguishable from a flash that
    did nothing. `tools/board_flash.py` does it, from `[flash]` data.
  - **The rollback confirm fires from the FRAME LOOP**, not the boot path:
    `confirm_when_healthy(ws._frames_drawn)` needs `HEALTHY_PAINTS` frames on the
    glass AND `HEALTHY_LOOPS` iterations survived. Confirming where the desktop is
    merely CONSTRUCTED certifies an image that never drew a pixel (#56). **The
    paint threshold cannot be raised** — the console repaints only on change, so
    a quiet desktop sits at ONE painted frame indefinitely and a paint-based gate
    would roll back every update nobody was touching. The loop counter carries the
    wait.
  - **A HEADLESS board confirms on different evidence, through the same body**
    (the Zero, 2026-08-29). The frame gate is unreachable there in BOTH
    directions: a made-up frame count certifies every image, a zero rolls every
    image back. `confirm_when_serving(serving)` counts `HEALTHY_SERVES` poll
    iterations of a live store host, and a host that falls over RESETS the count
    rather than pausing it. Both gates end in one `_confirm()`, so the marker's
    lifetime below is not re-implemented. **Do NOT merge them behind a flag** —
    the argument list is where the claim about the hardware lives.
  - **With no screen the update UI is two pin-gated JSON endpoints**
    (`zero_host.ZeroUpdate`): the POST queues and answers, the work runs in the
    poll loop, and the last verdict is a field rather than a banner. The board's
    README states the trigger decision and its reasoning.
  - **`finish()` writes `pending.json` naming the slot it pointed the bootloader
    at**; `boot_check()` compares it against the running slot next boot. **The
    marker is cleared at the CONFIRM, not at the read**, so an image that boots,
    reports and then dies still carries its evidence into the boot after the
    rollback. The verdict surfaces as a notice banner and again on Settings →
    UPDATE.
  - **Two channels, and the channel is a BUILD choice**: STABLE from master,
    UNSTABLE/BETA from dev, stamped into a gitignored `modules/_ota_build.py` from
    `MOYBYTE_OTA_CHANNEL` — clean across merges, never a per-branch source edit.
    An install is offered when the manifest's channel DIFFERS from the running one
    (a switch, including beta→stable rollback) **or** is higher WITHIN the channel.
    A card's `ota.json` always WINS over the baked url, which is how a LAN or
    offline host overrides it — so **delete a leftover one before testing the
    real path**, or it silently reroutes every check.
  - **The manifest is SIGNED, and the BOARD is inside the signature** (scheme
    `moybyte-ota-v2`): an OTA payload is an app-partition image, so another
    board's is a valid image that cannot boot, and a manifest naming one is
    refused BY NAME before the signature is even checked. `ssl.wrap_socket` does
    no certificate verification on device, which is *why* the manifest is signed
    rather than trusted for arriving over TLS.
  - **RSA, not Ed25519, purely for the verifier**: `pow(sig, 65537, n)` is a
    handful of modular squarings MicroPython does in C, where pure-Python curve
    arithmetic would take seconds. **Signing needs the `release` extra; verifying
    needs nothing**, which is what lets the security-critical half be tested in
    ordinary CI (`tests/test_ota_signing.py`, `tests/test_moy_ota.py`).
  - **The url and label are deliberately UNSIGNED** so a classroom can mirror the
    official manifest to a LAN host and rewrite the url — the bytes stay pinned
    by the signed hash. **Policy:** a manifest from a BAKED channel url must be
    signed; one reached because the owner put an `ota.json` on the card need not
    be (writing to the card is a physical act of consent, and it keeps the
    key-free LAN dev loop working) — but a signature that IS present is always
    checked, so a tampered official manifest cannot be laundered through a local
    host. A build with no baked key cannot require one.
  - **`OTA_PUBLIC_KEYS` is a TUPLE so a key can be ROTATED** — publish an image
    trusted by the old key and signed by the new.
  - **`ensure_online()` must WAIT for the link** after autoconnect
    (`ONLINE_WAIT_MS`): `DeviceWifi.connect()` polls briefly and gives up, and a
    saved network that comes up just after reads as "wifi offline". The wait
    belongs there, not in `connect()`, which would freeze the desktop on every
    wrong password.
  - **Bump `moy_ota.FIRMWARE_VERSION` only via `make release`** — a hand bump
    desynchronises the stamp CI reads back out of the artifact, and a manifest
    advertising a version the image does not carry offers the same install
    forever.

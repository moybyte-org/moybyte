# Hosting Moybyte off-box

**The always-on VM recipe that used to live here is gone.** It hosted the
live streaming web console tool, which was deleted in the
2026-08 streaming sunset (`docs/history/moycore_plan_2026-08.md` §3.2). The browser
console is now the **wasm head** (`firmware/web_runner`): the whole console
compiled to WebAssembly, running client-side. It is a static bundle — build it
with `firmware/web_runner/build.sh` and host `dist/` on anything that serves
files (Netlify / Pages / S3); `make site` already publishes it. No persistent
Python process, no WebSocket stepping frames, no VM, no auth problem.

## Firmware builds on GitHub Actions

`.github/workflows/firmware-build.yml` builds the ESP32 images on GitHub's
runners — no VM. It runs automatically on path-filtered pushes (master → the
`firmware-latest` stable release, dev → `firmware-beta`; the workflow header
is the authority), or from the **Actions** tab → *Firmware build* → *Run
workflow* for an unpublished one-off `.bin` of `tdeck`, `p4`, or `both`
(artifacts `moybyte-firmware-<board>`, 14-day retention).

Note: the first run installs the ESP-IDF toolchains (~2–3 GB, cached after) so
it's the slow one; later runs ride the toolchain + ccache caches.

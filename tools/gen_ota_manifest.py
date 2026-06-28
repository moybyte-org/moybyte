#!/usr/bin/env python3
"""Generate the OTA update manifest (latest.json) for the device's WiFi updater (#53).

The device's Settings -> UPDATE ONLINE flow fetches a small JSON manifest and, if its
"version" is newer than the running kc_ota.FIRMWARE_VERSION, streams the referenced
.bin to SD and verifies it against the manifest's size + sha256. This tool computes
those fields from the built image so the manifest can never drift from the binary.

Per release:
    1. bump FIRMWARE_VERSION in kc_ota.py, rebuild the firmware
    2. run this tool (it reads FIRMWARE_VERSION back out, so the manifest version
       always matches the image you actually built)
    3. upload the .bin + latest.json to your host

Usage:
    python tools/gen_ota_manifest.py [BIN] [--base-url URL | --url URL]
                                     [--version N] [--out PATH]

With no args it defaults to the built T-Deck app image and an http://<your-LAN-IP>:8000
base URL, so `make ota-manifest` + `make ota-serve` is a working local test loop.
"""

import argparse
import hashlib
import json
import re
import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FW_DIR = REPO_ROOT / "firmware" / "lilygo_t_deck_plus_micropython"
DEFAULT_BIN = FW_DIR / "dist" / "kidcode_micropython_tdeck.bin"
KC_OTA = FW_DIR / "modules" / "kc_ota.py"
DEFAULT_PORT = 8000


def read_firmware_version(kc_ota_path=KC_OTA):
    """Parse `FIRMWARE_VERSION = N` out of kc_ota.py so the manifest version matches
    the firmware being shipped. Raises if the constant can't be found."""
    text = Path(kc_ota_path).read_text(encoding="utf-8")
    m = re.search(r"^FIRMWARE_VERSION\s*=\s*(\d+)", text, re.MULTILINE)
    if not m:
        raise ValueError("FIRMWARE_VERSION not found in %s" % kc_ota_path)
    return int(m.group(1))


def detect_lan_ip():
    """Best-effort outbound LAN IP (no packets are actually sent). Falls back to
    127.0.0.1 so the tool still produces a manifest offline."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def build_manifest(bin_path, url, version):
    """The manifest dict the device consumes: version/url/size/sha256 (+ filename for
    humans). size + sha256 are computed from the actual bytes on disk."""
    data = Path(bin_path).read_bytes()
    return {
        "version": int(version),
        "url": url,
        "filename": Path(bin_path).name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _resolve_url(args, bin_path):
    if args.url:
        return args.url
    base = args.base_url or ("http://%s:%d" % (detect_lan_ip(), args.port))
    return base.rstrip("/") + "/" + Path(bin_path).name


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate the KidCode OTA manifest (latest.json).")
    ap.add_argument("bin", nargs="?", default=str(DEFAULT_BIN),
                    help="path to the app .bin (default: the built T-Deck image)")
    ap.add_argument("--base-url", help="host base URL; the .bin filename is appended "
                    "(default: http://<LAN-IP>:%d)" % DEFAULT_PORT)
    ap.add_argument("--url", help="full URL of the hosted .bin (overrides --base-url)")
    ap.add_argument("--version", type=int, help="manifest version "
                    "(default: read FIRMWARE_VERSION from kc_ota.py)")
    ap.add_argument("--out", help="output manifest path (default: latest.json beside the .bin)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="port for the default base URL (default: %d)" % DEFAULT_PORT)
    args = ap.parse_args(argv)

    bin_path = Path(args.bin)
    if not bin_path.exists():
        ap.error("image not found: %s\n  build it first: "
                 "KIDCODE_SKIP_VFS_BOOT=1 make firmware-build-lilygo-micropython" % bin_path)

    version = args.version if args.version is not None else read_firmware_version()
    url = _resolve_url(args, bin_path)
    manifest = build_manifest(bin_path, url, version)

    out = Path(args.out) if args.out else bin_path.with_name("latest.json")
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    base_for_manifest = url.rsplit("/", 1)[0]
    print("Wrote %s" % out)
    print(json.dumps(manifest, indent=2))
    print()
    print("Serve the folder containing the .bin + latest.json, e.g.:")
    print("    cd %s && python3 -m http.server %d" % (bin_path.parent, args.port))
    print()
    print("On the device SD card, set /sd/update/ota.json to:")
    print('    { "manifest_url": "%s/%s" }' % (base_for_manifest, out.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())

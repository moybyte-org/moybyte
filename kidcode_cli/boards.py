"""Known hardware board profiles for KidCode tooling."""

import glob
import json
import os
import re
import shutil

from kidcode.errors import ManifestError
from kidcode.manifest import Manifest
from kidcode_cli.pack import pack_project

BOARD_PROFILES = {
    "lilygo_t_deck_plus": {
        "schema": "kidcode.board.v1",
        "id": "lilygo_t_deck_plus",
        "title": "LilyGO T-Deck Plus",
        "family": "lilygo_t_deck",
        "mcu": "esp32s3",
        "platformio_env": "T-Deck",
        "flash_size": "16MB",
        "psram": "8M OPI PSRAM",
        "sources": [
            "https://github.com/Xinyuan-LilyGO/T-Deck",
            "https://raw.githubusercontent.com/Xinyuan-LilyGO/T-Deck/master/boards/T-Deck.json",
            "https://raw.githubusercontent.com/Xinyuan-LilyGO/T-Deck/master/examples/UnitTest/utilities.h",
        ],
        "notes": [
            "Official LilyGO repo covers T-Deck and T-Deck-Plus.",
            "T-Deck-Plus assigns Grove interface pins to GPS, so Grove is not available.",
            "Use the official T-Deck PlatformIO environment as the first firmware base.",
        ],
        "pins": {
            "power_on": 10,
            "i2c_sda": 18,
            "i2c_scl": 8,
            "keyboard_int": 46,
            "sdcard_cs": 39,
            "tft_cs": 12,
            "tft_dc": 11,
            "tft_backlight": 42,
            "spi_mosi": 41,
            "spi_miso": 38,
            "spi_sck": 40,
            "gps_tx": 43,
            "gps_rx": 44,
            "boot": 0,
        },
    }
}


def get_board_profile(board_id):
    try:
        return BOARD_PROFILES[board_id]
    except KeyError as exc:
        raise ManifestError("unknown board: " + board_id) from exc


def board_profile_json(board_id):
    return json.dumps(get_board_profile(board_id), indent=2) + "\n"


def serial_ports():
    ports = []
    for pattern in ["/dev/ttyACM*", "/dev/ttyUSB*", "/dev/cu.usbmodem*", "/dev/cu.usbserial*"]:
        ports.extend(glob.glob(pattern))
    return sorted(set(ports))


def choose_serial_port():
    ports = serial_ports()
    if len(ports) == 1:
        return ports[0]
    return None


def device_doctor(board_id):
    profile = get_board_profile(board_id)
    return {
        "board": profile["id"],
        "title": profile["title"],
        "platformio": shutil.which("pio") or shutil.which("platformio"),
        "serial_ports": serial_ports(),
        "platformio_env": profile["platformio_env"],
    }


def smoke_check_log(path, board_id="lilygo_t_deck_plus", project_id=None):
    profile = get_board_profile(board_id)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    failures = []
    required = [
        "KidCode firmware smoke test",
        "Board id: " + profile["id"],
        "Display: ST7789 color heartbeat",
        "Runtime: serial-only scaffold",
        "KidCode heartbeat",
    ]
    if project_id is not None:
        required.append("Bundled project: " + project_id)
    for item in required:
        if item not in text:
            failures.append("missing serial text: " + item)
    match = re.search(r"Bundle bytes:\s*(\d+)", text)
    if match is None:
        failures.append("missing bundle byte count")
    elif int(match.group(1)) <= 0:
        failures.append("bundle byte count is zero")
    return failures


def export_device_project(project_path, board_id, out_dir):
    profile = get_board_profile(board_id)
    manifest = Manifest.load(project_path)
    out_dir = os.path.abspath(out_dir)
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    bundle_name = manifest.id + ".kc8"
    bundle_path = os.path.join(out_dir, bundle_name)
    pack_project(project_path, out_path=bundle_path)

    deploy = {
        "schema": "kidcode.device_export.v1",
        "board": profile["id"],
        "platformio_env": profile["platformio_env"],
        "project_id": manifest.id,
        "title": manifest.title,
        "bundle": bundle_name,
        "runner_contract": "docs/firmware_runtime_contract.md",
    }
    deploy_path = os.path.join(out_dir, "deploy.json")
    with open(deploy_path, "w", encoding="utf-8") as fh:
        json.dump(deploy, fh, indent=2)
        fh.write("\n")

    readme_path = os.path.join(out_dir, "README.md")
    with open(readme_path, "w", encoding="utf-8") as fh:
        fh.write("# KidCode Device Export\n\n")
        fh.write("Board: " + profile["title"] + "\n\n")
        fh.write("Bundle: `" + bundle_name + "`\n\n")
        fh.write("Use this directory as the firmware-side input for the KidCode runner.\n")
    return out_dir

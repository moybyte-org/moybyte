"""Firmware export helpers."""

import os
import tempfile

from kidcode.manifest import Manifest
from kidcode_cli.boards import get_board_profile
from kidcode_cli.pack import pack_project


def _c_identifier(value):
    out = []
    for ch in value:
        if ch.isalnum():
            out.append(ch.upper())
        else:
            out.append("_")
    name = "".join(out).strip("_")
    if not name or name[0].isdigit():
        name = "PROJECT_" + name
    return name


def _format_bytes(data):
    lines = []
    for index in range(0, len(data), 12):
        chunk = data[index : index + 12]
        lines.append("    " + ", ".join("0x%02x" % item for item in chunk))
    return ",\n".join(lines)


def write_bundle_header(project_path, board_id, out_path):
    profile = get_board_profile(board_id)
    manifest = Manifest.load(project_path)
    with tempfile.TemporaryDirectory() as tmp:
        bundle_path = os.path.join(tmp, manifest.id + ".kc8")
        pack_project(project_path, out_path=bundle_path)
        with open(bundle_path, "rb") as fh:
            data = fh.read()

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    guard = "KIDCODE_PROJECT_BUNDLE_" + _c_identifier(manifest.id) + "_H"
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("#pragma once\n")
        fh.write("#ifndef " + guard + "\n")
        fh.write("#define " + guard + "\n\n")
        fh.write("#include <stddef.h>\n")
        fh.write("#include <stdint.h>\n\n")
        fh.write("#define KIDCODE_PROJECT_ID \"" + manifest.id + "\"\n")
        fh.write("#define KIDCODE_PROJECT_TITLE \"" + manifest.title + "\"\n")
        fh.write("#define KIDCODE_PROJECT_BOARD \"" + profile["id"] + "\"\n")
        fh.write("#define KIDCODE_PROJECT_BUNDLE_SIZE " + str(len(data)) + "\n\n")
        fh.write("static const uint8_t KIDCODE_PROJECT_BUNDLE[] = {\n")
        fh.write(_format_bytes(data))
        fh.write("\n};\n\n")
        fh.write("#endif\n")
    return out_path

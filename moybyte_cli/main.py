"""Command line interface for Moybyte."""

import argparse
import os
import sys

from moybyte.errors import MoybyteRuntimeError, ManifestError
from moybyte.manifest import Manifest
from moybyte_blocks.compiler import compile_project
from moybyte_cli.boards import (
    board_profile_json,
    device_doctor,
    export_device_project,
    smoke_check_log,
)
from moybyte_cli.firmware import write_bundle_header
from moybyte_cli.pack import pack_project
from moybyte_cli.portable import check_path
from moybyte_cli.projects import create_project
from moybyte_sim.main import run_project


def _display_path(path):
    rel = os.path.relpath(path)
    if rel.startswith(".."):
        return os.path.abspath(path)
    return rel


# (module, label, what it is for). REQUIRED = installed by `make setup` (the
# dev + sim extras); OPTIONAL = extras you only need for a specific job.
_REQUIRED_MODULES = [
    ("pytest", "pytest", "make test"),
    ("pygame", "pygame", "the simulator window (`.[sim]`)"),
    ("PIL", "pillow", "GIF export: --gif / make site-gifs"),
]
_OPTIONAL_MODULES = [
    ("lupa", "lupa", "host-side Lua carts; tests/test_lua_*.py skip without it"),
    ("esptool", "esptool", "flashing a board (`.[device]`)"),
    ("serial", "pyserial", "serial monitor / on-glass tests (`.[device]`)"),
]


def _probe_module(name):
    """Import a module quietly; return (version, file) or None."""
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    try:
        module = __import__(name)
    except Exception:
        return None
    return (str(getattr(module, "__version__", "?")), getattr(module, "__file__", "") or "")


def _cmd_doctor(_args):
    print("Moybyte doctor")
    print("python: " + sys.version.split()[0] + " (" + sys.executable + ")")

    # The venv itself is a check: `--system-site-packages` lets a dependency that
    # only exists on THIS machine look installed, which is how the simulator
    # shipped for a long time without pygame ever being installed by `make setup`.
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    leaky = False
    if in_venv:
        try:
            with open(os.path.join(sys.prefix, "pyvenv.cfg")) as handle:
                leaky = "include-system-site-packages = true" in handle.read()
        except OSError:
            pass
        print("venv: " + sys.prefix + (" [inherits system site-packages]" if leaky else ""))
        if leaky:
            print("  warning: packages from outside this venv are visible here, so a")
            print("  dependency can look installed when a clean machine would not have")
            print("  it. `make setup` builds a plain venv -- consider re-running it.")
    else:
        print("venv: none (system python) -- the docs assume ./.venv, see `make setup`")

    missing = []
    for name, label, why in _REQUIRED_MODULES:
        found = _probe_module(name)
        if found is None:
            missing.append(label)
            print(label + ": MISSING -- needed for " + why)
        else:
            version, path = found
            outside = in_venv and path and not path.startswith(sys.prefix + os.sep)
            print(label + ": " + version + (" [outside the venv: " + path + "]" if outside else ""))
    for name, label, why in _OPTIONAL_MODULES:
        found = _probe_module(name)
        print(label + ": " + (found[0] if found else "not installed") + " (optional -- " + why + ")")

    if missing:
        print("")
        print("missing: " + ", ".join(missing))
        print("  fix: " + sys.executable + " -m pip install -e '.[dev,sim]'   (or: make setup)")
        return 1
    return 0


def _cmd_validate(args):
    manifest = Manifest.load(args.project)
    print("valid: " + manifest.title + " (" + manifest.id + ")")
    return 0


def _cmd_board_info(args):
    print(board_profile_json(args.board), end="")
    return 0


def _cmd_device_doctor(args):
    info = device_doctor(args.board)
    print("Moybyte device doctor")
    print("Board: " + info["title"] + " (" + info["board"] + ")")
    print("PlatformIO env: " + info["platformio_env"])
    if info["platformio"]:
        print("PlatformIO: " + info["platformio"])
    else:
        print("PlatformIO: not found")
    if info["serial_ports"]:
        print("Serial ports:")
        for port in info["serial_ports"]:
            print("  " + port)
    else:
        print("Serial ports: none found")
    return 0


def _cmd_device_port(_args):
    ports = device_doctor("lilygo_t_deck_plus")["serial_ports"]
    if not ports:
        print("no serial ports found", file=sys.stderr)
        return 1
    if len(ports) > 1:
        print("multiple serial ports found:", file=sys.stderr)
        for port in ports:
            print("  " + port, file=sys.stderr)
        print("set PORT explicitly", file=sys.stderr)
        return 2
    print(ports[0])
    return 0


def _cmd_lilygo_next(_args):
    info = device_doctor("lilygo_t_deck_plus")
    print("Moybyte LilyGO next step")
    print("Board: " + info["title"])
    if not info["platformio"]:
        print("PlatformIO is not available. Run `make setup` and install PlatformIO before flashing.")
        return 1
    ports = info["serial_ports"]
    if not ports:
        print("No serial port is visible yet.")
        print("1. Connect the T-Deck Plus over USB-C.")
        print("2. Power it on.")
        print("3. If upload mode is needed, hold the center trackball and press reset.")
        print("4. Run `make device-port` again.")
        return 1
    if len(ports) > 1:
        print("Multiple serial ports are visible:")
        for port in ports:
            print("  " + port)
        print("Pick the T-Deck Plus port and run:")
        print("  make firmware-smoke-lilygo PORT=<port>")
        return 2
    port = ports[0]
    print("Detected serial port: " + port)
    print("Run:")
    print("  make firmware-smoke-lilygo PORT=" + port)
    print("Expected screen: centered 128x128 tiny_runner canvas with moving green player.")
    return 0


def _cmd_export_device(args):
    out_dir = export_device_project(args.project, args.board, args.out)
    print("exported: " + _display_path(out_dir))
    return 0


def _cmd_firmware_header(args):
    out_path = write_bundle_header(args.project, args.board, args.out)
    print("generated: " + _display_path(out_path))
    return 0


def _cmd_firmware_smoke_check(args):
    failures = smoke_check_log(args.log, board_id=args.board, project_id=args.project_id)
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print("firmware smoke check passed")
    return 0


def _cmd_new(args):
    project_dir = create_project(
        args.project,
        project_id=args.project_id,
        title=args.title,
        kind=args.kind,
        age_mode=args.age_mode,
    )
    print("created: " + project_dir)
    return 0


def _cmd_run(args):
    if args.frames is not None and args.frames <= 0:
        print("--frames must be greater than zero", file=sys.stderr)
        return 2
    if args.fps <= 0:
        print("--fps must be greater than zero", file=sys.stderr)
        return 2
    if args.scale is not None and args.scale <= 0:
        print("--scale must be greater than zero", file=sys.stderr)
        return 2
    try:
        context = run_project(
            args.project,
            headless=args.headless,
            frames=args.frames,
            entry=args.entry,
            fps=args.fps,
            scale=args.scale,
        )
    except RuntimeError as exc:   # no simulator window available -- say so, don't traceback
        print("error: " + str(exc), file=sys.stderr)
        return 3
    print("ran: " + context.manifest.id + " frames=" + str(context.frame))
    if context.audio.calls:
        print("audio calls:")
        for call in context.audio.calls:
            print("  " + repr(call))
    return 0


def _cmd_compile(args):
    out_path = compile_project(args.project)
    print("generated: " + _display_path(out_path))
    return 0


def _cmd_pack(args):
    out_path = pack_project(
        args.project,
        out_path=args.out,
        include_generated=args.include_generated,
    )
    print("packed: " + _display_path(out_path))
    return 0


def _cmd_check_portable(args):
    issues = []
    for path in args.paths:
        issues.extend(check_path(path))
    for issue in issues:
        print(issue)
    if issues:
        print("portable check failed: " + str(len(issues)) + " issue(s)", file=sys.stderr)
        return 1
    print("portable check passed")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="moybyte")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=_cmd_doctor)

    validate = sub.add_parser("validate")
    validate.add_argument("project")
    validate.set_defaults(func=_cmd_validate)

    board_info = sub.add_parser("board-info")
    board_info.add_argument("board")
    board_info.set_defaults(func=_cmd_board_info)

    device_doctor_cmd = sub.add_parser("device-doctor")
    device_doctor_cmd.add_argument("--board", default="lilygo_t_deck_plus")
    device_doctor_cmd.set_defaults(func=_cmd_device_doctor)

    device_port = sub.add_parser("device-port")
    device_port.set_defaults(func=_cmd_device_port)

    lilygo_next = sub.add_parser("lilygo-next")
    lilygo_next.set_defaults(func=_cmd_lilygo_next)

    export_device = sub.add_parser("export-device")
    export_device.add_argument("project")
    export_device.add_argument("--board", default="lilygo_t_deck_plus")
    export_device.add_argument("--out", required=True)
    export_device.set_defaults(func=_cmd_export_device)

    firmware_header = sub.add_parser("firmware-header")
    firmware_header.add_argument("project")
    firmware_header.add_argument("--board", default="lilygo_t_deck_plus")
    firmware_header.add_argument("--out", required=True)
    firmware_header.set_defaults(func=_cmd_firmware_header)

    firmware_smoke_check = sub.add_parser("firmware-smoke-check")
    firmware_smoke_check.add_argument("log")
    firmware_smoke_check.add_argument("--board", default="lilygo_t_deck_plus")
    firmware_smoke_check.add_argument("--project-id", default="tiny_runner")
    firmware_smoke_check.set_defaults(func=_cmd_firmware_smoke_check)

    new = sub.add_parser("new")
    new.add_argument("project")
    new.add_argument("--id", dest="project_id")
    new.add_argument("--title")
    new.add_argument("--kind", choices=["game", "app", "demo", "tool"], default="game")
    new.add_argument("--age-mode", choices=["cards", "blocks", "text", "advanced"], default="text")
    new.set_defaults(func=_cmd_new)

    run = sub.add_parser("run")
    run.add_argument("project")
    run.add_argument("--headless", action="store_true")
    run.add_argument("--frames", type=int)
    run.add_argument("--entry")
    run.add_argument("--fps", type=int, default=30)
    run.add_argument("--scale", type=int)
    run.set_defaults(func=_cmd_run)

    compile_cmd = sub.add_parser("compile")
    compile_cmd.add_argument("project")
    compile_cmd.set_defaults(func=_cmd_compile)

    pack = sub.add_parser("pack")
    pack.add_argument("project")
    pack.add_argument("--out")
    pack.add_argument("--include-generated", action="store_true")
    pack.set_defaults(func=_cmd_pack)

    check_portable = sub.add_parser("check-portable")
    check_portable.add_argument("paths", nargs="+")
    check_portable.set_defaults(func=_cmd_check_portable)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ManifestError as exc:
        print("manifest error: " + str(exc), file=sys.stderr)
        return 2
    except MoybyteRuntimeError as exc:
        friendly = exc.friendly_error
        print(str(friendly), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

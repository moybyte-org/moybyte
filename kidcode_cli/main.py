"""Command line interface for KidCode."""

import argparse
import os
import sys

from kidcode.errors import KidCodeRuntimeError, ManifestError
from kidcode.manifest import Manifest
from kidcode_blocks.compiler import compile_project
from kidcode_cli.portable import check_path
from kidcode_cli.projects import create_project
from kidcode_sim.main import run_project


def _cmd_doctor(_args):
    print("KidCode doctor")
    print("Python: " + sys.version.split()[0])
    try:
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame  # noqa: F401

        print("pygame: available")
    except Exception:
        print("pygame: not installed (headless simulator is available)")
    return 0


def _cmd_validate(args):
    manifest = Manifest.load(args.project)
    print("valid: " + manifest.title + " (" + manifest.id + ")")
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
    context = run_project(
        args.project,
        headless=args.headless,
        frames=args.frames,
        entry=args.entry,
        fps=args.fps,
        scale=args.scale,
    )
    print("ran: " + context.manifest.id + " frames=" + str(context.frame))
    if context.audio.calls:
        print("audio calls:")
        for call in context.audio.calls:
            print("  " + repr(call))
    return 0


def _cmd_compile(args):
    out_path = compile_project(args.project)
    print("generated: " + os.path.relpath(out_path))
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
    parser = argparse.ArgumentParser(prog="kidcode")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=_cmd_doctor)

    validate = sub.add_parser("validate")
    validate.add_argument("project")
    validate.set_defaults(func=_cmd_validate)

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
    except KidCodeRuntimeError as exc:
        friendly = exc.friendly_error
        print(str(friendly), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

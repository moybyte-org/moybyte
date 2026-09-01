"""Drag-and-drop PICO-8 import, in the browser (#194).

The whole feature, on this side, is three calls the worker makes in order --
`problem_with()`, `convert()`, and whatever the page does with the report. What
it is NOT is a converter, a porter or a writer: all three are the SAME code the
CLI runs (`tools/p8_import.py` and `tools/p8_lua_port.py`, moy-spec's and
hash-pinned; `tools/p8_writer.py`, ours -- the guards and the report), staged
into this build's frozen set by build.sh. There is no browser variant of any of
them, on purpose -- this repo has already paid for a second copy of the
converter once (ten days of carts two octaves flat, `tools/import_p8.py`'s
header), and a JavaScript reimplementation of the PNG unpack would be the same
mistake with a language barrier on top.

The imported cart RUNS (owner call, 2026-08-29): `p8_lua_port` emits the cart's
own Lua under a generated PICO-8 shim, so a drop lands a cart that plays rather
than a porting exercise.

WHY THE PNG IS INFLATED HERE AND NOT IN JS. The browser could skip the inflate
entirely -- `createImageBitmap` + a canvas `getImageData` hands the pixels over
and JS could read the low bits into the ROM. It would also be a SECOND reader of
one file format: `_p8png_rom` already does exactly that, and the two would be
free to disagree. `shims/zlib.py` (four lines over MicroPython's built-in
`deflate`) closes the only gap instead, so both cart forms converge on
`read_p8()` here exactly as they do on a desktop -- and the same shim is what a
board will need when the device leg lands, where there is no `createImageBitmap`
to lean on at all. Measured under a real MicroPython: 40ms to read a `.p8.png`,
13ms to write the cart.

The imported cart is LOCAL AND PRIVATE (#194). It lands in this browser's own
store like any cart made here, and nothing in this file offers to publish it.
"""

import json

import p8_import
import p8_writer

# Where a dropped file is parked while the converter reads it. It is deleted on
# the way out (both ways out) rather than left for the store sweep to find: the
# carts watcher walks the cart root, and a stray .p8.png under it is neither a
# cart nor something the far end should ever be sent.
TMP_DIR = "/moy/tmp"


class P8Problem(Exception):
    """An import that cannot happen, with a sentence a kid can read."""


def _rm(path):
    import os
    try:
        os.remove(path)
    except OSError:
        pass


def problem_with(path, name):
    """None when `path` looks like a PICO-8 cart, else a sentence for the page.

    #194's "report, don't crash": the answer to a file that is not a cart is a
    line of prose, never the crash-to-code throw. The PNG half matters most --
    the converter validates with `assert`, and THIS BUILD FREEZES AT opt=3, which
    compiles asserts out, so without these the failure would surface somewhere
    deep in a struct unpack with nothing naming the file."""
    f = open(path, "rb")
    try:
        head = f.read(8)
    finally:
        f.close()
    if p8_writer.looks_like_png(head):
        f = open(path, "rb")
        try:
            return p8_writer.png_problem(f.read())
        finally:
            f.close()
    if name.lower().endswith(".png"):
        return "that file is named .png but is not a PNG"
    return None


def _why(exc):
    """`exc` as a sentence fragment that always names something.

    MicroPython raises several builtins with NO message -- UnicodeError is the
    one that mattered -- and `"%s" % exc` on those is the empty string, so the
    report read "that cart did not decode ()" and named neither the fault nor
    the file. A user cannot act on that and neither can a maintainer: it cost a
    full debugging session to learn that the empty parens meant UnicodeError.
    Fall back to the type, which is never empty.
    """
    return str(exc) or type(exc).__name__


def convert(path, name, out_dir):
    """A dropped `.p8` / `.p8.png` at `path` -> a `.moy` folder at `out_dir`.

    Returns the writer's summary dict, or raises `P8Problem` with a sentence.
    `out_dir` is chosen by the caller (the worker, through the store's own
    collision-safe naming) so importing a cart twice behaves like importing a
    zip twice."""
    problem = problem_with(path, name)
    if problem:
        raise P8Problem(problem)
    try:
        sections = p8_import.read_p8(path)
    except SystemExit as exc:
        # NOT an Exception subclass -- caught by name or not at all, which is
        # why it is named here even though the converter no longer has a
        # reachable one (it refused `pxa` this way until 2026-08-30, when it
        # learned to read it). Kept because the day one comes back, a bare
        # `except Exception` would let it kill the worker instead of the import.
        raise P8Problem(str(exc) or "that cart uses a code compression this "
                                    "importer cannot read")
    except Exception as exc:                 # noqa: BLE001
        # A cart that IS the right shape and still does not decode -- a
        # truncated download, a hand-edited PNG, a `pxa` stream whose
        # back-reference points before the start. The converter raises for
        # those now rather than refusing up front, and a raise that reaches the
        # worker is a dead console, not a message.
        raise P8Problem("that cart did not decode (%s)" % (_why(exc),))
    problem = p8_writer.sections_problem(sections)
    if problem:
        raise P8Problem(problem)
    return p8_writer.write_cart(sections, out_dir,
                                p8_writer.cart_title(sections, name))


def import_p8_json(path, name, out_dir):
    """The worker's whole entry point: convert, tidy up, report.

    One JSON string out, because that is the only thing that crosses the wasm
    boundary cheaply: `{"ok": true, "title": ..., "report": [...]}` or
    `{"ok": false, "report": ["..."]}`. Never raises -- a failed import is a
    message, exactly like a failed export, because the console is fine and it
    was the ask that was not."""
    try:
        summary = convert(path, name, out_dir)
    except P8Problem as exc:
        _rm(path)
        return json.dumps({"ok": False, "report": [str(exc)]})
    except Exception as exc:  # noqa: BLE001 -- a broken cart is not a crash
        _rm(path)
        return json.dumps({"ok": False, "report": [
            "that cart could not be imported (%s)" % (_why(exc),)]})
    _rm(path)
    return json.dumps({"ok": True, "title": summary["title"],
                       "report": p8_writer.report_lines(summary)})

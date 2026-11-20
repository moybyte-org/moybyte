"""The import direction between the runtime's packages.

runtime/ is one flat module namespace kept in folders (runtime/README.md),
and device/ is the boards' own tier beside it. A folder is a layer here: a
module imports its own folder and the layers below it, never a layer above
and never a sibling in its own layer. Every crossing that stays is named in
CROSSINGS by the module it reaches, with the reason and the modules that may
make it; any other crossing fails, and so does a named one no module makes
any more.

The order, from the bottom:

  host      the host's stand-ins for the boards' C modules (never staged),
            and the simulator's runners, named where they reach up
  carts     the store and the cart formats
  toolkit   the draw toolkit, which reads the picture and sheet formats
  uitree    the UI tree API (`import uitree as ui`) and its replay into
            the toolkit's verbs
  system    the system's own surfaces and services
  run       running a cart
  editors   the Editor and its cores
  shell     the console kernel and its services, which build what is above
  wm, home, apps   what the console builds, siblings
  device    the boards' tier: the desk spine (desktop_spine, wire_input)
            builds the console
  boot      the boot spine and the dev channel

Imports are read with `ast`, wherever they sit (a function's lazy import
counts), as the bare names a board resolves them by.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LAYERS = (
    ("host",),
    ("carts",),
    ("toolkit",),
    ("uitree",),
    ("system",),
    ("run",),
    ("editors",),
    ("shell",),
    ("wm", "home", "apps"),
    ("device",),
    ("boot",),
)

# module reached: (why it may be reached from below, the modules that may)
CROSSINGS = {
    "ticks": ("the clock shim: it imports only `time`",
              ("cart_index", "cart_unpack", "face_ctypes", "moy_input", "wasm_host")),
    "moy_index": ("the C index's host binding raises the Python twin's errors and keeps its limits",
                  ("native_parts",)),
    "elf_run": ("the simulator runs a native cart's ELF as a board does",
                ("host_app",)),
    "wasi_run": ("the compiled-cart runner hosts a WASI app as a board does",
                 ("wasm_host",)),
    "wm_windowed": ("the simulator builds the desk's window manager",
                    ("host_app",)),
    "device_canvas": ("the one canvas class every tier runs (docs/surface_model_v1.md §4)",
                      ("cover_png", "wallpaper", "host_canvas", "wasm_host")),
    "moy_ota": ("the firmware's version and its update state, which About, Settings, the backup and the permission table read",
                ("moy_backup", "app_perms", "about_ui", "settings_layer", "wallpaper")),
    "device_util": ("the board's tick and timing helpers a netplay session reads",
                    ("players",)),
    "device_wifi": ("the kernel's WLAN handle a netplay session joins over",
                    ("players",)),
    "moycore_glue": ("the Lua and compiled tiers' frame glue, which the host's runners drive as a board does",
                     ("host_app", "wasm_host")),
    "blocks": ("a blocks cart's code is compiled from its blocks as the store reads it",
               ("moy_carts",)),
    "face_errors": ("the faces' error mapping is staged with the system, where a board's faces read it",
                    ("moy_app_binding", "moy_app_face")),
    "lua_ext": ("the compiled-cart runner shares the Lua tier's frame half",
                ("wasm_host",)),
    "audio_session": ("Settings' volume row is the console volume",
                      ("settings_layer",)),
    "text_modes": ("an intent opens text in the editor mode its kind names",
                   ("intents",)),
    "editor_handle": ("the cart-facing text editor is a leaf over the Files role and the cart's canvas",
                      ("player",)),
    "app_perms": ("the permission table every tier checks a cart against, and the tier a "
                  "cart runs on, which the Player and the wallpaper route alike",
                  ("elf_run", "player", "player_pace", "achievements", "perms_ui",
                   "sched_python", "scheduler", "wallpaper")),
    "layers": ("the Layer base every surface subclasses",
               ("cards_layer", "code_layer", "editor_desk", "paint_layer", "theme_editor",
                "settings_layer", "tasks_ui", "uitree_app")),
    "editors_code": ("a `ui.doc` is the Code tab's text machinery under one object",
                     ("uitree_doc",)),
    "op_history": ("a `ui.doc`'s undo is the op journal the cart editor handle keeps",
                   ("uitree_doc",)),
    "artwork": ("an `image` value of a drawing decodes through My Art's cover reader",
                ("uitree_pics",)),
    "bar_layer": ("the bar a surface lays its verbs on",
                  ("cards_layer", "editor_app", "update_ui")),
    "chrome": ("the console's stateless base: the palette and the responsive layout",
               ("theme_editor", "cart_api", "web_console")),
    "chrome_ops": ("the kernel's chrome as the shell draws it, over a cart too",
                   ("player", "player_fit")),
    "system_api": ("the globals a user app cart gets, which the Player and the scheduler hand it",
                   ("player", "scheduler")),
    "idle_ladder": ("Settings' idle rungs step through the ladder's values",
                    ("settings_rows",)),
    "crash_guard": ("three strikes for an app, a service or a wallpaper alike",
                    ("scheduler", "settings_layer")),
    "theme_custom": ("the theme an Appearance change makes, which the theme editor writes",
                     ("theme_editor",)),
    "wm": ("the console builds its window manager",
           ("console",)),
    "launcher_layer": ("the console builds the home",
                       ("console",)),
    "home_carousel": ("the home service builds the home's arrangements",
                      ("console_home",)),
    "home_grid": ("the home service builds the home's arrangements",
                  ("console_home",)),
    "home_icons": ("the home service draws the shelf's icons",
                   ("console_home",)),
    "text_console": ("the console builds the surface a script cart runs on",
                     ("console",)),
    "music_app": ("a media run found, the console has Music's card and status item follow it",
                  ("console",)),
    "device_boot": ("the desk spine runs the boot spine's steps",
                    ("desktop_spine",)),
    "dev_channel": ("the desk spine opens the dev channel",
                    ("desktop_spine",)),
}


def _modules():
    """Every module's basename and its folder."""
    out = {}
    for path in sorted((ROOT / "runtime").glob("*/*.py")) + sorted((ROOT / "device").glob("*.py")):
        folder = path.parent.name if path.parent.parent.name == "runtime" else "device"
        assert path.stem not in out, "two modules named %s" % path.stem
        out[path.stem] = (folder, path)
    return out


def _imported(tree):
    """(line, name) for every module the tree imports, by its bare name."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                yield node.lineno, parts[1] if parts[0] == "runtime" and len(parts) > 1 else parts[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            parts = node.module.split(".")
            if parts == ["runtime"]:
                for alias in node.names:
                    yield node.lineno, alias.name
            elif parts[0] == "runtime":
                yield node.lineno, parts[1]
            else:
                yield node.lineno, parts[0]


def _rank():
    return {folder: i for i, layer in enumerate(LAYERS) for folder in layer}


def _crossings():
    """(importer, imported, where) for every import that leaves its folder for
    a layer that is not below it."""
    mods = _modules()
    rank = _rank()
    out = []
    for name, (folder, path) in mods.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, other in _imported(tree):
            if other not in mods or mods[other][0] == folder:
                continue
            if rank[mods[other][0]] >= rank[folder]:
                out.append((name, other, "%s:%d" % (path.relative_to(ROOT), line)))
    return out


def test_every_folder_has_a_layer():
    folders = {folder for folder, _path in _modules().values()}
    assert folders == set(_rank()), sorted(folders ^ set(_rank()))


def test_no_import_crosses_up_a_layer_undeclared():
    bad = sorted("%s imports %s (%s)" % (where, other, name)
                 for name, other, where in _crossings()
                 if name not in CROSSINGS.get(other, ("", ()))[1])
    assert not bad, (
        "an import goes up a layer or across to a sibling (tests/test_layers.py "
        "has the order); import the other way, or name the crossing and its "
        "reason in CROSSINGS:\n  " + "\n  ".join(bad))


def test_every_declared_crossing_is_made():
    made = {(name, other) for name, other, _where in _crossings()}
    stale = sorted("%s -> %s" % (name, other)
                   for other, (_why, names) in CROSSINGS.items()
                   for name in names if (name, other) not in made)
    assert not stale, "declared crossings no module makes: " + ", ".join(stale)


def test_every_crossing_has_a_reason():
    assert all(why.strip() and names for why, names in CROSSINGS.values())

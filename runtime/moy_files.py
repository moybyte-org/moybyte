"""The #108 user-files layer: the kid's creations as real files under ONE
visible root beside the carts dir -- `files/<kind>/<name><ext>` -- plus a
PROJECT's own folder as a files-role kind. The kinds registry (`FILE_KINDS`),
the vault's whole-name rule for scripts and typed documents, list/load/save
and the auto-naming verbs. The per-file history sidecars, the trash and the
provenance stamps are `moy_file_ops`; `moy_carts` re-exports both under the
old names.
"""

try:
    import os
except ImportError:  # pragma: no cover
    os = None

try:
    from moy_fs import (_exists, _mkdir, _read, _read_recover, _write_atomic)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_fs import (_exists, _mkdir, _read, _read_recover, _write_atomic)
try:
    from moy_journal import (journal_append)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_journal import (journal_append)
try:
    from moy_store_base import (CARTS_DIR, FLAGS_NAME, IMAGES_DIR, IMAGE_EXT, SCENES_DIR, SCENE_EXT, _is_dir, _sibling_path, ensure_dirs, slug)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import (CARTS_DIR, FLAGS_NAME, IMAGES_DIR, IMAGE_EXT, SCENES_DIR, SCENE_EXT, _is_dir, _sibling_path, ensure_dirs, slug)


# --- user files (#108): the kid's creations as real files -------------------
#
# Creations that outlive any one app or cart (a Paint drawing, a note, a
# recorded voice set) live under ONE visible root BESIDE the carts dir --
# files/<kind>/<name><ext> -- real folders with real names on the card, so the
# same stuff a File Manager shows is what a PC sees on the mounted SD. Kinds
# are flat (no nesting in v1) and kind-homed like every desktop OS's known
# folders. Carts never reference these: reuse is copy-on-use through the
# existing attach verbs (the one exception, recordings, is used-by-name and
# never copied INTO a cart -- #70's privacy rule). Delete moves to
# files/trash/<kind>/ (restorable; pruned by count), never destroys directly.

FILES_DIR = "files"
TRASH_DIR = "trash"
TRASH_KEEP = 50          # prune the trash's oldest entries beyond this many

# Documents are PLAIN MARKDOWN (2026-09-07): `files/docs/<name>.md`, UTF-8, LF,
# no envelope and no header -- the file's body IS the document and its stem is
# the note's name. The point is that a card in a PC's reader opens the same
# folder in Obsidian with nothing to convert. The reader is STRICT: a `.md` is
# the only thing the docs kind lists, so the `.moytext` wrapper this replaced is
# not a document any more, it is a file the vault does not know.
DOC_EXT = ".md"

# SCRIPTS live in the vault beside the notes (docs/text_editing_2026-09.md): a
# bare `.py` or `.lua` file is a cart with no folder, and RUN wraps it on the
# fly. They are the ONE kind of vault item listed under its WHOLE name --
# `hello.py`, not `hello` -- because the extension is what says which runtime
# runs it, and because a note called `hello` and a script called `hello.py` are
# two different things that must not shadow each other.
SCRIPT_EXTS = (".py", ".lua")

# The vault holds more than notes. A `.txt`, a `.json` and a script are all
# things a person makes ON the console (the NEW prompt in Notes takes a name
# and honours the extension it carries), and every one of them is listed and
# addressed under its WHOLE name -- `todo.txt`, never `todo` -- because the
# extension is what picks the editing MODE, and because two files that differ
# only by it must not shadow each other.
#
# `.md` is the ONE extension a vault name may leave off: it is what a bare
# name MEANS. So a note stays `story`, and the list shows what each item is
# through its badge (text_modes.badge) rather than by spelling `.md` on every
# row.
VAULT_EXTS = SCRIPT_EXTS + (".txt", ".json")


def script_ext(name):
    """The script extension `name` carries, or "" -- the one place that says a
    vault item is a PROGRAM and not prose."""
    return _ext_from(name, SCRIPT_EXTS)


def vault_ext(name):
    """The extension `name` keeps in the VAULT, or "" -- what says the name is
    already whole and nothing is appended to it on disk."""
    return _ext_from(name, VAULT_EXTS)


def _ext_from(name, exts):
    name = str(name)
    for ext in exts:
        if name.endswith(ext) and len(name) > len(ext):
            return ext
    return ""


def _item_ext(kind, name):
    """The on-disk extension for one item of `kind`. The kind's own, except for
    a whole-named vault item, which already carries its extension."""
    if kind == "docs" and vault_ext(name):
        return ""
    return _kind_spec(kind)[0]


def _whole_exts(kind):
    """Extensions `kind` lists under their whole name -- the vault's."""
    return VAULT_EXTS if kind == "docs" else ()


def _split_item(kind, name):
    """`(stem, ext)` for a whole-named vault item, `(name, "")` otherwise --
    where a uniquifying counter goes, so a collision yields `todo_2.txt` and
    not `todo.txt_2` (which would be stored as `todo.txt_2.md`)."""
    ext = vault_ext(name) if kind == "docs" else ""
    return (str(name)[:-len(ext)], ext) if ext else (str(name), "")


def _slug_item(kind, name):
    """`slug` for a file item, keeping a whole-named vault item's extension
    (slug drops the dot, so slugging the whole name would turn `hello.py` into
    `hellopy` and lose the runtime with it)."""
    stem, ext = _split_item(kind, name)
    return (slug(stem) + ext) if ext else slug(name)

# kind -> (extension, folder_valued, auto-name base). A folder-valued kind
# (#70 recordings) holds one DIRECTORY per item (the macOS-bundle model); file
# kinds hold one flat file per item. Every store verb below validates against
# this registry, so an unknown kind is a loud ValueError, not a stray dir.
FILE_KINDS = {
    "drawings":   (IMAGE_EXT, False, "drawing"),
    "docs":       (DOC_EXT, False, "doc"),
    "sprites":    (".moygfx", False, "sheet"),
    "music":      (".moysong", False, "song"),
    "recordings": ("", True, "recording"),
}


def _kind_spec(kind):
    try:
        return FILE_KINDS[kind]
    except KeyError:
        raise ValueError("unknown file kind: " + str(kind))


# -- a PROJECT's own files, as a files-role KIND ------------------------------
#
# The Config tab's ADVANCED row (step 5 of docs/text_editing_2026-09.md) edits a
# cart's own manifest.json / config.json / main file in the shell's editor
# handle, and the handle is identified by `(kind, name)` through the Files role.
# So a project becomes a kind: `project:<folder>.moy`, whose store is that
# folder under the carts root rather than a `files/<kind>/` directory.
#
# It is a KIND and not a path because the handle, the parked text request and
# the cart-facing `open_editor` all speak `(kind, name)` and none of them may
# learn about directories. The folder rather than the full path so the token is
# root-relative: the same request means the same file on the host and on a board
# whose carts live somewhere else.
#
# The two things this kind deliberately does NOT get:
#   * a `files/.history/` sidecar -- a project file's durable undo is the
#     project's own JOURNAL (#111), which `save_project_file` appends to. Two
#     parallel histories over one file would double-count every edit, and
#     `project:foo.moy` is not a legal FAT directory name anyway.
#   * the trash / rename / duplicate / auto-name verbs. A cart's own files are
#     named by the FORMAT, not by a person, so there is nothing to name and
#     nothing that may go missing.
PROJECT_KIND = "project:"

# The order the ADVANCED row lists a project in: the two documents a person
# edits, the program, then its assets. Anything else on disk follows, sorted.
PROJECT_ORDER = ("manifest.json", "config.json", "main.py", "main.lua",
                 "sprites.moygfx", "map.moymap", FLAGS_NAME, "sounds.json",
                 "blocks.json")

# Subfolders whose items the row lists as `<dir>/<name><ext>`.
PROJECT_SUBDIRS = ((SCENES_DIR, SCENE_EXT), (IMAGES_DIR, IMAGE_EXT))

# The atomic-write machinery's orphans, and the two DIRECTORIES a listing must
# never wander into (the journal's snapshots are history, not files to edit).
_PROJECT_SKIP_EXT = (".bak", ".tmp")


def project_kind(path_or_folder):
    """The files-role kind naming the project at `path_or_folder` -- a cart
    dict's `path` or a bare `.moy` folder name."""
    name = str(path_or_folder)
    cut = max(name.rfind("/"), name.rfind("\\"))
    return PROJECT_KIND + (name[cut + 1:] if cut >= 0 else name)


def project_folder(kind):
    """The `.moy` folder `kind` names, or "" when `kind` is a user-files kind.
    The ONE predicate that says "this kind is a project", so every store verb
    branches on the same answer."""
    k = str(kind)
    return k[len(PROJECT_KIND):] if k.startswith(PROJECT_KIND) else ""


def project_dir(kind, root=CARTS_DIR):
    folder = project_folder(kind)
    if not folder:
        raise ValueError("not a project kind: " + str(kind))
    return root + "/" + folder


def project_file_path(kind, name, root=CARTS_DIR):
    """The on-disk path of one project file. `name` may carry ONE subfolder
    (`scenes/opening.moyscene`) and nothing else: a name that climbs, or that
    is absolute, is refused rather than resolved, because this kind is the one
    place a NAME chosen elsewhere becomes a path."""
    parts = str(name).replace("\\", "/").split("/")
    if len(parts) > 2 or not parts[-1] or parts[0] in ("", ".", ".."):
        raise ValueError("bad project file name: " + str(name))
    if len(parts) == 2 and parts[1] in ("", ".", ".."):
        raise ValueError("bad project file name: " + str(name))
    return project_dir(kind, root) + "/" + "/".join(parts)


def list_project_files(kind, root=CARTS_DIR):
    """One project's own files, PROJECT_ORDER first and the rest sorted after.

    Lists what is actually on disk rather than what a cart could hold, so a
    main file the manifest renamed still appears and an absent asset is not a
    dead row."""
    d = project_dir(kind, root)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    flat = []
    for n in names:
        if _ends_any(n, _PROJECT_SKIP_EXT) or _is_dir(d + "/" + n):
            continue
        flat.append(n)
    out = [n for n in PROJECT_ORDER if n in flat]
    out.extend(n for n in flat if n not in PROJECT_ORDER)
    for sub, ext in PROJECT_SUBDIRS:
        try:
            kids = sorted(os.listdir(d + "/" + sub))
        except OSError:
            continue
        out.extend(sub + "/" + n for n in kids if n.endswith(ext))
    return out


def load_project_file(kind, name, root=CARTS_DIR):
    """One project file's text, or None -- through `_read_recover`, so a crash
    mid-save reads the `.bak` exactly as `load()` does for the manifest."""
    try:
        return _read_recover(project_file_path(kind, name, root))
    except OSError:
        return None


def save_project_file(kind, name, text, root=CARTS_DIR):
    """Write one project file atomically and record it in the project's undo
    journal (#111), which is the shape `Project.commit_config` already has for
    config.json. A journal failure never fails the write -- the edit is on
    disk, the kid just loses one undo step."""
    path = project_file_path(kind, name, root)
    _write_atomic(path, text)
    try:
        journal_append(project_dir(kind, root), str(name), text)
    except Exception as exc:  # noqa: BLE001 -- journaling can't fail a save
        print("Moybyte project journal failed:", exc)
    return str(name)


def files_root(root=CARTS_DIR):
    """The user-files root, beside the carts directory (like shared.moygfx)."""
    return _sibling_path(root, FILES_DIR)


def file_kind_dir(kind, root=CARTS_DIR):
    _kind_spec(kind)
    return files_root(root) + "/" + kind


def file_path(kind, name, root=CARTS_DIR):
    return file_kind_dir(kind, root) + "/" + name + _item_ext(kind, name)


def _ensure_kind_dir(kind, root):
    ensure_dirs(root)
    _mkdir(files_root(root))
    d = file_kind_dir(kind, root)
    _mkdir(d)
    return d


def _mtime(path):
    try:
        return os.stat(path)[8]
    except OSError:
        return 0


def _kind_entries(d, ext, folder_valued, whole=()):
    """[(name, mtime)] of the kind's items in `d`, newest first (mtime is
    best-effort -- 0 on filesystems without one, leaving alphabetical order).
    Skips the atomic-write machinery's .tmp/.bak orphans by construction: a
    file item must end with the kind's extension exactly.

    `whole` names extra extensions the kind holds whose items keep their WHOLE
    name -- the vault's scripts, and nothing else so far."""
    try:
        names = os.listdir(d)
    except OSError:
        return []
    out = []
    for n in names:
        p = d + "/" + n
        if folder_valued:
            if _is_dir(p):
                out.append((n, _mtime(p)))
        elif _ends_any(n, whole) and not _is_dir(p):
            out.append((n, _mtime(p)))
        elif n.endswith(ext) and len(n) > len(ext) and not _is_dir(p):
            out.append((n[:-len(ext)] if ext else n, _mtime(p)))
    out.sort(key=lambda e: (-e[1], e[0]))
    return out


def _ends_any(name, exts):
    for ext in exts:
        if name.endswith(ext) and len(name) > len(ext):
            return True
    return False


def list_files(kind, root=CARTS_DIR):
    """The kind's item names, newest first."""
    if project_folder(kind):
        return list_project_files(kind, root)
    ext, folder_valued, _base = _kind_spec(kind)
    d = file_kind_dir(kind, root)
    entries = _kind_entries(d, ext, folder_valued, _whole_exts(kind))
    return [n for n, _m in entries]


def count_files(kind, root=CARTS_DIR):
    """How many items the kind holds -- a bare listdir filter, so the Files
    kinds screen never pays list_files' per-item stat+sort just for a badge."""
    if project_folder(kind):
        return len(list_project_files(kind, root))
    ext, folder_valued, _base = _kind_spec(kind)
    d = file_kind_dir(kind, root)
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    if folder_valued:
        return sum(1 for n in names if _is_dir(d + "/" + n))
    whole = _whole_exts(kind)
    return sum(1 for n in names
               if _ends_any(n, whole)
               or (n.endswith(ext) and len(n) > len(ext)))


def load_file(kind, name, root=CARTS_DIR):
    """A file item's text, or None if missing/unreadable (degrade-don't-throw,
    like every asset loader). Folder-valued kinds have no single blob."""
    if project_folder(kind):
        return load_project_file(kind, name, root)
    if _kind_spec(kind)[1]:
        return None
    try:
        return _read(file_path(kind, name, root))
    except OSError:
        return None


def _unique_name(kind, name, root, path=None):
    """`name` if free under `path` (default: the kind's live dir), else name_2,
    name_3, ... -- the ONE collision probe every rename/duplicate/trash move
    rides on (`path` swaps in _trash_path for the trash side)."""
    path = path or file_path
    if not _exists(path(kind, name, root)):
        return name
    stem, ext = _split_item(kind, name)
    i = 2
    while _exists(path(kind, stem + "_" + str(i) + ext, root)):
        i += 1
    return stem + "_" + str(i) + ext


def new_file_name(kind, root=CARTS_DIR, base=None):
    """The next free auto-name for the kind (drawing_1, drawing_2, ...) --
    creations are auto-named so naming is never a gate; rename is optional."""
    _ext, _fv, kind_base = _kind_spec(kind)
    base = slug(base) if base else kind_base
    i = 1
    while _exists(file_path(kind, base + "_" + str(i), root)):
        i += 1
    return base + "_" + str(i)


def free_file_name(kind, title, root=CARTS_DIR):
    """The name a TYPED title lands on: slugged the kind's way (a vault
    extension survives), then unique-ified -- so NEW never silently overwrites
    a file that is already there, and a name a person can no longer read is
    auto-named instead."""
    for ch in str(title):
        if ch.isalpha() or ch.isdigit():
            break
    else:
        return new_file_name(kind, root)
    return _unique_name(kind, _slug_item(kind, title), root)


def save_file(kind, name, text, root=CARTS_DIR):
    """Persist one file item atomically (folder-valued kinds are written by
    their own tools, never through this). Returns the (slugged) stored name."""
    if project_folder(kind):
        # A project file keeps its name verbatim -- the FORMAT chose it, so
        # slugging it would write `manifestjson` and take the cart down.
        return save_project_file(kind, name, text, root)
    if _kind_spec(kind)[1]:
        raise ValueError(kind + " items are folders; write them in place")
    name = _slug_item(kind, name)
    _ensure_kind_dir(kind, root)
    _write_atomic(file_path(kind, name, root), text)
    return name

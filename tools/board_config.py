"""Read a board's `board.toml` and stage its modules (#161 Phase 3).

WHY THIS EXISTS. A board's definition used to live in six places in three
languages (issue #161), and the piece that hurt first was the STAGING LIST:
each board's `build.sh` named, by hand, every shared `runtime/*.py` it wanted
frozen. An allowlist answers the wrong question. It asks "did somebody remember
to add this module?" when the question is "is there a reason this module must
not cross?" -- so a new shared module is invisible to a board until someone
edits a shell script, and the failure is silent, because every consumer in this
tree is capability-gated and a missing module reads as a missing FEATURE.

So the boards now do what `firmware/web_runner/build.sh` already did: stage
everything, DENY what must not cross, and record the reason beside the denial.
`board.toml` holds both -- the list and the prose -- because #161's own sketch
says the rationale has to move with the data ("a board file with the
constraints stripped out is worse than what we have now").

WHY THE PARSER IS IN HERE. `board.toml` is read by a build that may be running
on nothing but the system `python3`: `requires-python` is >=3.10, `tomllib`
arrived in 3.11, and `tomli` is not a declared dependency. A third-party import
in the build path would mean a board that cannot be built without `make setup`.
So this file parses the small TOML subset the board files actually use, in the
stdlib, and `tests/test_board_toml.py` cross-checks it against real `tomllib`/
`tomli` when one is importable -- which it is in the dev venv and in CI, since
pytest itself depends on `tomli` below 3.11. One implementation everywhere,
verified by an independent one.

Usage from a build script:

    python3 tools/board_config.py stage firmware/<board>/

Usage from a test:

    from tools.board_config import staged_modules
    staged_modules(TDECK)      # {"console.py": Path(".../runtime/console.py")}
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# A minimal TOML reader: tables, arrays of tables, strings, string arrays,
# booleans, integers. Deliberately NOT a full implementation -- see the module
# docstring for why it exists at all, and test_board_toml.py for the check that
# it agrees with the real thing on the files it actually reads.
# ---------------------------------------------------------------------------


class TomlError(ValueError):
    pass


class _Parser:
    def __init__(self, text):
        self.s = text
        self.n = len(text)
        self.i = 0

    # -- lexing helpers ------------------------------------------------------

    def _skip(self, newlines=True):
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t\r":
                self.i += 1
            elif c == "\n":
                if not newlines:
                    return
                self.i += 1
            elif c == "#":
                while self.i < self.n and self.s[self.i] != "\n":
                    self.i += 1
            else:
                return

    def _expect(self, lit):
        if not self.s.startswith(lit, self.i):
            raise TomlError("expected %r at offset %d" % (lit, self.i))
        self.i += len(lit)

    def _bare_key(self):
        start = self.i
        while self.i < self.n and (self.s[self.i].isalnum()
                                   or self.s[self.i] in "_-"):
            self.i += 1
        if self.i == start:
            raise TomlError("expected a key at offset %d" % self.i)
        return self.s[start:self.i]

    def _key_path(self):
        path = [self._bare_key()]
        while self.i < self.n and self.s[self.i] == ".":
            self.i += 1
            path.append(self._bare_key())
        return path

    # -- values --------------------------------------------------------------

    def _string(self):
        if self.s.startswith('"""', self.i):
            self.i += 3
            if self.s.startswith("\n", self.i):      # TOML trims one leading NL
                self.i += 1
            end = self.s.find('"""', self.i)
            if end < 0:
                raise TomlError("unterminated multi-line string")
            out = self.s[self.i:end]
            self.i = end + 3
            return _unescape(out)
        if self.s.startswith("'''", self.i):
            self.i += 3
            if self.s.startswith("\n", self.i):
                self.i += 1
            end = self.s.find("'''", self.i)
            if end < 0:
                raise TomlError("unterminated multi-line literal string")
            out = self.s[self.i:end]
            self.i = end + 3
            return out
        quote = self.s[self.i]
        self.i += 1
        buf = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\" and quote == '"':
                buf.append(self.s[self.i:self.i + 2])
                self.i += 2
                continue
            if c == quote:
                self.i += 1
                return _unescape("".join(buf)) if quote == '"' else "".join(buf)
            if c == "\n":
                raise TomlError("newline in a single-line string")
            buf.append(c)
            self.i += 1
        raise TomlError("unterminated string")

    def _array(self):
        self._expect("[")
        out = []
        while True:
            self._skip(True)
            if self.i >= self.n:
                raise TomlError("unterminated array")
            if self.s[self.i] == "]":
                self.i += 1
                return out
            out.append(self._value())
            self._skip(True)
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1

    def _value(self):
        self._skip(False)
        c = self.s[self.i]
        if c in "\"'":
            return self._string()
        if c == "[":
            return self._array()
        if self.s.startswith("true", self.i):
            self.i += 4
            return True
        if self.s.startswith("false", self.i):
            self.i += 5
            return False
        start = self.i
        while self.i < self.n and self.s[self.i] not in ",]\n#":
            self.i += 1
        raw = self.s[start:self.i].strip()
        try:
            return int(raw, 0)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            raise TomlError("unsupported value %r at offset %d" % (raw, start))

    # -- statements ----------------------------------------------------------

    def parse(self):
        root = {}
        cur = root
        while True:
            self._skip(True)
            if self.i >= self.n:
                return root
            if self.s.startswith("[[", self.i):
                self.i += 2
                path = self._key_path()
                self._expect("]]")
                parent = _descend(root, path[:-1])
                cur = {}
                parent.setdefault(path[-1], []).append(cur)
            elif self.s[self.i] == "[":
                self.i += 1
                path = self._key_path()
                self._expect("]")
                cur = _descend(root, path)
            else:
                key = self._bare_key()
                self._skip(False)
                self._expect("=")
                cur[key] = self._value()


def _descend(root, path):
    node = root
    for part in path:
        nxt = node.setdefault(part, {})
        if isinstance(nxt, list):            # [[a.b]] then [a.b.c]: last table
            nxt = nxt[-1]
        node = nxt
    return node


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _unescape(s):
    if "\\" not in s:
        return s
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in _ESCAPES:
                out.append(_ESCAPES[nxt])
                i += 2
                continue
            if nxt == "\n":                  # line-continuation in a multiline
                i += 2
                while i < len(s) and s[i] in " \t\r\n":
                    i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def loads(text):
    """Parse the TOML subset the board files use."""
    return _Parser(text).parse()


# ---------------------------------------------------------------------------
# The board file itself.
# ---------------------------------------------------------------------------


def load(board_dir):
    """The parsed `board.toml` of `board_dir`."""
    path = Path(board_dir) / "board.toml"
    return loads(path.read_text(encoding="utf-8"))


def _shared(cfg):
    return cfg.get("modules", {}).get("shared", {})


def denials(board_dir):
    """{filename: entry} -- every shared module this board refuses, with why."""
    return {e["file"]: e for e in _shared(load(board_dir)).get("deny", [])}


def renames(board_dir):
    """{source filename: destination filename} for modules staged under
    another name (`font.py` -> `moy_font.py`, which is what the device's
    `moy_gfx.text` kernel imports)."""
    return {e["file"]: e["as"] for e in _shared(load(board_dir)).get("rename", [])}


def staged_modules(board_dir, root=ROOT):
    """{destination filename: source Path} -- what a FRESH build stages.

    Shared modules come from the denylist over `runtime/*.py`; device modules
    come from the per-group allowlist over another board's `modules/` tree
    (see the board file for why THAT one stays an allowlist).
    """
    board_dir, root = Path(board_dir), Path(root)
    cfg = load(board_dir)
    shared = _shared(cfg)
    out = {}

    deny = {e["file"] for e in shared.get("deny", [])}
    ren = {e["file"]: e["as"] for e in shared.get("rename", [])}
    src_dir = root / shared.get("source", "runtime")
    for p in sorted(src_dir.glob("*.py")):
        if p.name in deny or p.name == "__init__.py":
            continue
        out[ren.get(p.name, p.name)] = p

    device = cfg.get("modules", {}).get("device", {})
    if device:
        dev_dir = root / device["source"]
        for group in device.get("group", []):
            for name in group["files"]:
                out[name] = dev_dir / name
    return out


def staged_packages(board_dir, root=ROOT):
    """{package name: source dir} -- directory-shaped modules staged whole."""
    cfg = load(board_dir)
    device = cfg.get("modules", {}).get("device", {})
    if not device:
        return {}
    dev_dir = Path(root) / device["source"]
    return {name: dev_dir / name for name in device.get("packages", [])}


# ---------------------------------------------------------------------------
# Staging (what build.sh calls).
# ---------------------------------------------------------------------------


def _tracked(dest):
    """Files git tracks under `dest` -- the board-AUTHORED modules, which the
    prune must never touch.

    Asked from INSIDE `dest` rather than repo-relative, so the answer is right
    for any checkout layout and the prune can be exercised against a throwaway
    tree in a test. No git, or no repo, returns None -- and a prune that cannot
    tell authored from staged does not run at all, because the failure mode of
    guessing is deleting somebody's source file.
    """
    try:
        out = subprocess.run(["git", "ls-files"], cwd=str(dest),
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {Path(line).name for line in out.split() if line}


def stage(board_dir, root=ROOT, quiet=False):
    """Stage this board's modules into its `modules/` tree, and prune strays."""
    board_dir, root = Path(board_dir).resolve(), Path(root)
    cfg = load(board_dir)
    mods = cfg.get("modules", {})
    dest = board_dir / mods.get("dest", "modules")
    dest.mkdir(parents=True, exist_ok=True)

    wanted = staged_modules(board_dir, root)
    missing = sorted(n for n, p in wanted.items() if not p.exists())
    if missing:
        raise SystemExit("board.toml stages files that do not exist: %s"
                         % ", ".join(missing))
    for name, src in sorted(wanted.items()):
        shutil.copyfile(src, dest / name)

    for name, src in sorted(staged_packages(board_dir, root).items()):
        pkg = dest / name
        if pkg.exists():
            shutil.rmtree(pkg)
        shutil.copytree(src, pkg,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    removed = []
    if mods.get("prune"):
        keep = set(mods.get("keep", [])) | set(wanted)
        tracked = _tracked(dest)
        if tracked is None:
            print("board_config: git unavailable -- skipping the stale prune")
        else:
            for p in sorted(dest.glob("*.py")):
                if p.name in keep or p.name in tracked:
                    continue
                p.unlink()
                removed.append(p.name)
    shutil.rmtree(dest / "__pycache__", ignore_errors=True)

    if not quiet:
        n_deny = len(_shared(cfg).get("deny", []))
        print("staged %d modules into %s (%d shared modules denied by "
              "board.toml)" % (len(wanted), dest.relative_to(root)
                               if str(dest).startswith(str(root)) else dest,
                               n_deny))
        if removed:
            print("  pruned %d stale staged file(s) no build produces any "
                  "more: %s" % (len(removed), ", ".join(removed)))
    return wanted, removed


def main(argv):
    if len(argv) >= 3 and argv[1] == "stage":
        stage(argv[2])
        return 0
    if len(argv) >= 3 and argv[1] == "list":
        for name in sorted(staged_modules(argv[2])):
            print(name)
        return 0
    if len(argv) >= 3 and argv[1] == "get":
        # `get <board_dir> a.b.c` -- one scalar, for a build script.
        cfg = load(argv[2])
        node = cfg
        for part in argv[3].split("."):
            node = node[part]
        print(node)
        return 0
    print(__doc__.strip().splitlines()[0], file=sys.stderr)
    print("usage: board_config.py stage|list <board_dir>", file=sys.stderr)
    print("       board_config.py get <board_dir> <dotted.key>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

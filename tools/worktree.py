#!/usr/bin/env python3
"""A worktree that tests and builds with no further setup, and its removal.

    tools/worktree.py new NAME [--from dev]   .claude/worktrees/NAME on branch NAME
    tools/worktree.py rm NAME [--force]       remove it and its build trees
    tools/worktree.py prune [--dry-run]       remove every worktree whose work is landed

`new` gives the worktree, beside the checkout git makes:

  * LINKED to the main checkout's (read-only toolchains, and caches whose
    entries are keyed by their inputs, so two trees cannot disagree in one):
    `.venv` (its editable install maps `runtime` to the MAIN checkout, so a
    script that does not put its own tree's root first on `sys.path` imports
    the main checkout's `runtime`); the wasm toolchain (`experiments/wasm_aot/toolchain/{dist,
    wasi-sdk}`) and the WAMR fork clone (`experiments/wasm_aot/wamr`); every
    ESP-IDF and emsdk under `firmware/*/.build/` (the web runner's build moves
    a linked emsdk to its pinned version, for every tree that shares it); the
    host caches in `.build/` named in SHARED_CACHES; the perf carts in
    `ports/p8perf`.
  * ITS OWN: every MicroPython tree the main checkout has
    (`firmware/*/.build/micropython`, `.build/unix_micropython/micropython`),
    each a local clone of the main checkout's at the same commit -- hardlinked
    objects, its submodules cloned from the main checkout's the same way, and
    the stock sources checked out. A build patches its own tree (every patcher
    is idempotent from stock) and writes its own build output, `dist/` and the
    browser bundle: nothing a build here does reaches the main checkout.
  * BUILT: the browser console (`firmware/web_runner/build.sh`, `--no-web`
    skips it), because every board image bakes the tree's own bundle.

`rm` refuses a worktree with uncommitted changes to tracked files, or with
commits not landed on `--into` (default dev, read as `origin/dev` when the
checkout has that remote branch, fetched first: a worktree pushes dev
without moving the main checkout's), unless `--force`. A commit is landed
when its patch is there (`git cherry`, so a cherry-picked landing counts) or
a commit with its subject is (a landing rebased through a conflict). It unlinks the
links first, removes the tree with every build output in it, deletes the
branch when nothing on it is unmerged, drops the CI container's volumes for
it (tools/ci_image/run.sh), and prints the space freed.

`prune` removes every worktree `rm` would remove without `--force` and that
no running process has its working directory in (a worker's live tree).

Run either from the main checkout or from any worktree: the main checkout is
found through git.
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Linked whole: toolchains a build only reads.
TOOLCHAINS = (
    ".venv",
    "experiments/wasm_aot/toolchain/dist",
    "experiments/wasm_aot/toolchain/wasi-sdk",
    "experiments/wasm_aot/wamr",
    "ports/p8perf",
)
TOOLCHAIN_GLOBS = ("firmware/*/.build/esp-idf", "firmware/*/.build/emsdk")
# Host build caches under .build/ whose entries are keyed by what built them
# (native_build's content hash, the WAMR pin, jet_cart's key) and taken under
# a lock, so a worktree with other sources adds entries instead of
# overwriting the main checkout's.
SHARED_CACHES = ("host", "host_audio", "host_wamr", "jet", "jet_cart")
# Every board image names the digest of the tree's own browser console bundle.
WEB_BUILD = "firmware/web_runner/build.sh"
MPY_GLOBS = ("firmware/*/.build/micropython", ".build/unix_micropython/micropython")


class WorktreeError(RuntimeError):
    """A refusal, in words a person can act on."""


def git(*args, cwd=None, check=True):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                       text=True)
    if check and r.returncode != 0:
        raise WorktreeError("git %s: %s" % (" ".join(args),
                                            (r.stderr or r.stdout).strip()))
    return r.stdout.strip()


def main_checkout(start=HERE):
    """The main checkout of the repository `start` is in."""
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir",
                 cwd=start)
    return os.path.dirname(os.path.realpath(common))


def worktrees_dir(main):
    return os.path.join(main, ".claude", "worktrees")


def du(path):
    """Bytes under `path`, symlinks not followed."""
    r = subprocess.run(["du", "-s", "--block-size=1", path],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.split()[0])
    except (IndexError, ValueError):
        return 0


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


# -- what a new worktree gets --------------------------------------------------


def links(main):
    """[(relative path)] of everything a worktree links to the main checkout,
    for what the main checkout actually has."""
    out = [p for p in TOOLCHAINS if os.path.lexists(os.path.join(main, p))]
    for pat in TOOLCHAIN_GLOBS:
        out += sorted(os.path.relpath(p, main)
                      for p in glob.glob(os.path.join(main, pat)))
    out += [os.path.join(".build", c) for c in SHARED_CACHES
            if os.path.isdir(os.path.join(main, ".build", c))]
    return out


def mpy_trees(main):
    out = []
    for pat in MPY_GLOBS:
        out += sorted(os.path.relpath(p, main)
                      for p in glob.glob(os.path.join(main, pat))
                      if os.path.isdir(os.path.join(p, ".git")))
    return out


def link(main, wt, rel):
    dst = os.path.join(wt, rel)
    if os.path.lexists(dst):
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.symlink(os.path.join(main, rel), dst)


def clone_mpy(src, dst):
    """A local clone of `src` at its HEAD, stock sources checked out, its
    initialised submodules cloned from `src`'s own."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    head = git("rev-parse", "HEAD", cwd=src)
    git("clone", "-q", "--no-checkout", src, dst)
    git("checkout", "-q", "--detach", head, cwd=dst)
    for line in git("submodule", "status", cwd=src).splitlines():
        if line.startswith("-"):
            continue
        path = line.split()[1]
        names = git("config", "-f", ".gitmodules", "--get-regexp",
                    r"submodule\..*\.path", cwd=dst).splitlines()
        name = next((k[len("submodule."):-len(".path")]
                     for k, v in (n.split(None, 1) for n in names) if v == path),
                    None)
        if name is None:
            continue
        gitdir = git("rev-parse", "--absolute-git-dir",
                     cwd=os.path.join(src, path))
        git("config", "submodule.%s.url" % name, gitdir, cwd=dst)
        git("-c", "protocol.file.allow=always", "submodule", "update", "-q",
            "--init", path, cwd=dst)


def cmd_new(a, main):
    wt = os.path.join(worktrees_dir(main), a.name)
    if os.path.lexists(wt):
        raise WorktreeError("%s already exists" % wt)
    if git("branch", "--list", a.name, cwd=main):
        raise WorktreeError("branch %s already exists -- pick another name, "
                            "or `git worktree add` it by hand" % a.name)
    git("worktree", "add", "-q", "-b", a.name, wt, a.base, cwd=main)
    for rel in links(main):
        link(main, wt, rel)
    trees = mpy_trees(main)
    for rel in trees:
        clone_mpy(os.path.join(main, rel), os.path.join(wt, rel))
    web = os.path.join(wt, WEB_BUILD)
    web_note = "not built (--no-web)"
    if os.path.isfile(web) and not a.no_web:
        os.makedirs(os.path.join(wt, ".build"), exist_ok=True)
        with open(os.path.join(wt, ".build", "web_build.log"), "w") as log:
            rc = subprocess.call(["bash", web], cwd=wt, stdout=log,
                                 stderr=subprocess.STDOUT)
        web_note = ("built" if rc == 0 else
                    "BUILD FAILED, see .build/web_build.log")
    sha = git("rev-parse", "--short=8", "HEAD", cwd=wt)
    print("%s  branch %s at %s (%s)" % (wt, a.name, sha, a.base))
    print("linked: %s" % ", ".join(links(main)))
    print("own MicroPython trees: %s" % ", ".join(
        t.split("/")[1] if t.startswith("firmware/") else "unix" for t in trees))
    if os.path.isfile(web):
        print("browser console: %s" % web_note)
    print("size: %s; a board build adds its own output beside it" % human(du(wt)))
    return 0


# -- removing one --------------------------------------------------------------


def registered(main):
    """{path: branch} of every worktree git knows, the main checkout aside."""
    out, path = {}, None
    for line in git("worktree", "list", "--porcelain", cwd=main).splitlines():
        if line.startswith("worktree "):
            path = os.path.realpath(line[len("worktree "):])
            out[path] = None
        elif line.startswith("branch ") and path:
            out[path] = line[len("branch refs/heads/"):]
    out.pop(os.path.realpath(main), None)
    return out


def dirty(wt):
    """Tracked files that differ from HEAD."""
    return git("status", "--porcelain", "--untracked-files=no", cwd=wt).splitlines()


def untracked(wt):
    """Files in `wt` that git neither tracks nor ignores: a worker's scripts
    and notes, which no commit would bring back."""
    out = git("status", "--porcelain", "--untracked-files=normal", cwd=wt)
    return [l for l in out.splitlines() if l.startswith("??")]


def upstream(main, into):
    """The ref `into` names for landing: its remote branch when the checkout
    has one (fetched, quietly; offline keeps the last fetch), else itself."""
    remote = "origin/" + into
    if git("rev-parse", "--verify", "--quiet", remote, cwd=main, check=False):
        subprocess.run(["git", "fetch", "--quiet", "origin", into], cwd=main,
                       capture_output=True)
        return remote
    return into


def unmerged(wt, into):
    """Commits at the worktree's HEAD that are not landed on `into`: no
    commit there carries their patch or their subject."""
    ahead = [ln[2:] for ln in git("cherry", into, "HEAD", cwd=wt).splitlines()
             if ln.startswith("+ ")]
    if not ahead:
        return []
    landed = set(git("log", "--format=%s", "-n", "5000", into, cwd=wt).splitlines())
    return [sha for sha in ahead
            if git("log", "-1", "--format=%s", sha, cwd=wt) not in landed]


def in_use(wt, proc="/proc"):
    """Pids of running processes working in `wt`: their working directory is
    inside it, or their command line names a path inside it (a measurement
    run from another tree with this tree's tools)."""
    pids = []
    roots = {os.path.abspath(wt).rstrip(os.sep), os.path.realpath(wt)}
    marks = [(r + os.sep).encode() for r in roots]
    for pid in os.listdir(proc) if os.path.isdir(proc) else ():
        if not pid.isdigit():
            continue
        try:
            cwd = os.path.realpath(os.readlink(os.path.join(proc, pid, "cwd")))
        except OSError:
            cwd = ""
        try:
            with open(os.path.join(proc, pid, "cmdline"), "rb") as f:
                argv = f.read().replace(b"\0", b" ") + b" "
        except OSError:
            argv = b""
        if any((cwd + os.sep).startswith(r + os.sep) for r in roots) or \
                any(m in argv or m[:-1] + b" " in argv for m in marks):
            pids.append(int(pid))
    return pids


def made(wt):
    """When `wt` was made: its HEAD reflog's first entry (git rewrites the
    tree's `.git` file, so no file mtime holds it)."""
    log = os.path.join(git("rev-parse", "--absolute-git-dir", cwd=wt),
                       "logs", "HEAD")
    try:
        with open(log) as f:
            return float(f.readline().split(">", 1)[1].split()[0])
    except (OSError, IndexError, ValueError):
        return 0.0


def blockers(wt, into):
    """Why `wt` cannot go without --force: [] when nothing holds it."""
    why = []
    changed = dirty(wt)
    if changed:
        why.append("%d tracked file(s) changed" % len(changed))
    loose = untracked(wt)
    if loose:
        why.append("%d untracked file(s)" % len(loose))
    ahead = unmerged(wt, into)
    if ahead:
        why.append("%d commit(s) not on %s" % (len(ahead), into))
    return why


def cmd_rm(a, main):
    wt = os.path.realpath(os.path.join(worktrees_dir(main), a.name))
    known = registered(main)
    if wt not in known:
        raise WorktreeError("%s is not a worktree of %s" % (wt, main))
    into = upstream(main, a.into)
    why = blockers(wt, into)
    if why and not a.force:
        raise WorktreeError("%s has %s -- commit and land them, or --force"
                            % (a.name, " and ".join(why)))
    remove(main, wt, known[wt], unmerged(wt, into), into, a.name)
    return 0


def remove(main, wt, branch, ahead, into, name):
    before = du(wt)
    ci = os.path.join(wt, "tools", "ci_image", "run.sh")
    if os.path.isfile(ci) and shutil.which("docker"):
        subprocess.run([ci, "--rm-volumes"], capture_output=True)
    for rel in links(main):
        p = os.path.join(wt, rel)
        if os.path.islink(p):
            os.unlink(p)
    git("worktree", "remove", "--force", "--force", wt, cwd=main)
    if os.path.lexists(wt):
        shutil.rmtree(wt)
    note = ""
    if branch and not ahead:
        git("branch", "-D", branch, cwd=main)
        note = "; branch %s deleted" % branch
    elif branch:
        note = "; branch %s kept (%d commit(s) not on %s)" % (
            branch, len(ahead), into)
    print("removed %s: %s freed%s" % (name, human(before), note))


def cmd_prune(a, main):
    into = upstream(main, a.into)
    home = os.path.realpath(worktrees_dir(main)) + os.sep
    kept = 0
    for wt, branch in sorted(registered(main).items()):
        if not wt.startswith(home):
            continue
        name = os.path.basename(wt)
        why = blockers(wt, into)
        pids = in_use(wt)
        if pids:
            why.append("in use by pid %s" % ", ".join(map(str, pids[:3])))
        age = time.time() - made(wt)
        if age < a.min_age * 60:
            why.append("made %d min ago" % (age // 60))
        if why:
            kept += 1
            print("kept %s: %s" % (name, " and ".join(why)))
        elif a.dry_run:
            print("would remove %s" % name)
        else:
            remove(main, wt, branch, [], into, name)
    if not kept and not registered(main):
        print("no worktrees")
    return 0


def parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new", help="a worktree ready to test and build")
    p.add_argument("name")
    p.add_argument("--from", dest="base", default="dev",
                   help="the commit or branch it starts at (default dev)")
    p.add_argument("--no-web", action="store_true",
                   help="skip building the browser console")
    p = sub.add_parser("rm", help="remove a worktree and its build trees")
    p.add_argument("name")
    p.add_argument("--force", action="store_true",
                   help="remove it with uncommitted or unmerged work")
    p.add_argument("--into", default="dev",
                   help="the branch its commits count as merged into")
    p = sub.add_parser("prune", help="remove every worktree whose work is landed")
    p.add_argument("--dry-run", action="store_true", help="say, remove nothing")
    p.add_argument("--into", default="dev",
                   help="the branch its commits count as merged into")
    p.add_argument("--min-age", type=int, default=120, metavar="MIN",
                   help="keep a tree made fewer than MIN minutes ago: a "
                        "worker between commands holds no process (120)")
    return ap


def main(argv=None, start=HERE):
    a = parser().parse_args(argv)
    name = getattr(a, "name", "")
    if "/" in name or name.startswith("."):
        print("a worktree name is one path component", file=sys.stderr)
        return 2
    try:
        m = main_checkout(start)
        return {"new": cmd_new, "rm": cmd_rm, "prune": cmd_prune}[a.cmd](a, m)
    except WorktreeError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

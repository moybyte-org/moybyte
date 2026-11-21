# Map (grep -n a name to jump there):
#   -- the checks Get Carts shares   fit_why, depends_why, blocker, install_check, manifest_check, origin_of, gated
#   -- the store's trust             revocations, revoked_rows
#   -- the steps it shares           through, removed, landed, land_now: a store op a frame
#   Asked                            a question the terminal asked: the run ends, the line runs again
#   Stopped                          Ctrl-C came
#   Pkg                              the shell's `pkg`: the verbs over one installer
#   Pkg.update                       fetch every source's index into the cache
#   Pkg.install                      depends first, then each cart through cart_index.Install
"""pkg -- the console's packages (docs/os_2026-11/system.md §6): apt-shaped
verbs over the store's one installer, `runtime/carts/cart_index.py`, of which
Get Carts (runtime/apps/getcarts_app.py) is the GUI face and the shell's
`pkg` (runtime/shell/sh_roles.py) the command face. A repository is a URL
serving an index and the carts it lists; `system/sources` names them, one a
line (absent: cart_index.DEFAULT_INDEXES, moybyte-org/carts). A `.moy` is the
package.

  pkg update             fetch every source's index (its signature checked)
                         into the cache, `pkg/` beside the carts folder
  pkg list               every cart the cached indexes list: id, version,
                         where it stands here (installed, update, taken)
  pkg search WORD...     the ones whose id, name, description or tags hold
                         every WORD
  pkg install [-y] ID... each with what it `depends` on first: the checks
                         below, the licence of an external file, the
                         download, and the permission prompt (-y answers
                         yes to both)
  pkg remove ID...       the store's delete: the folder, its record, the
                         files it wrote, its consent; an optional built-in's
                         removal recorded (never a core one's)
  pkg upgrade [-y]       every installed cart the indexes list newer
  pkg sources            the sources, one a line

A verb that reads the indexes fetches a source the cache does not hold yet.

Every verb but `sources` is a generator its caller steps (the shell's door,
runtime/shell/sh_door.py): it yields while it waits on the network, never
waiting in one step -- the link asked for (`ready()`, the transport's
non-blocking poll, never `online()`), an index or a licence fetched whole by
the http worker (`http`: its `get`, runtime/system/dev_http.py) where the
console has one, an install a budget at a time -- and returns the exit code.

The checks every install makes, here and in Get Carts (`blocker`): a store to
keep it in, a runtime this console carries, a compiled module for this chip
(or a main.wasm the interpreter plays), the engine's own fit and THE SHARES'
(docs/os_2026-11/processes.md §6: a cart larger than this board's share of
memory for the running set can never be admitted), room in the store, and
its `depends` (cart_index.needs). At the install prompt (`install_check`), a
permission this console lacks refuses it, and so does a `"type": "command"`
cart whose name -- its id's part after the first dot -- the shell already
answers to or another installed command holds. A version its index revoked
is refused (cart_index.REVOKED).

A Python cart from an index that is not reviewed (cart_index.is_reviewed)
asks with the unreviewed warning (app_perms.UNREVIEWED), and where the
console has a password (`passwd`, the one SSH and a paired browser sign in
with) accepting it takes that password (`gated`): Get Carts asks for it in
the prompt, `pkg install` on the terminal (`-y` never answers it), a Zero's
page posts it with its yes. The acceptance is the consent record's
`unreviewed`, so an update from the same index asks again only when it adds
a permission. Reading an index marks the carts installed from it whose
version it revoked (`revocations`): their launches are refused until an
update or a removal.
"""

import hashlib
import json
import os

import cart_index as _ci
import moy_carts as _store
from moy_store_base import _sibling_path, cart_path as _cart_path
from moy_fs import _exists, _mkdir, _read, _remove
import app_keys as _keys
import app_perms as _perms
from player_fit import fit_notice as _fit_notice, board_share as _board_share

InstallError = _ci.InstallError
ASKED = object()                # an answer the terminal asks for: the run ends (Asked)


class Asked(Exception):
    """A question the session asked (moy_sh.h's moy_sh_ask): the run ends,
    and the line runs again with its answer."""


# Session -> (its index entry, as text, sha256s and all; its Install, waiting
# at the install prompt; the list its prompt's check filled): the build a
# line's question left checked, which the line's next run takes up in place
# of a second download. It goes when that run finishes it, or when the
# session's question is over (`forget`, the door's `unask`).
_KEPT = {}


def _drop(job):
    """An install that will not land, stopped and its build deleted."""
    job.cancel()
    while job.step(STEP_MS):
        pass


def forget(session):
    """Session `session`'s question is over (moy_sh.h's `unask`): the build
    kept for its line goes."""
    got = _KEPT.pop(session, None)
    if got is not None:
        _drop(got[1])
WRONG_PASSWORD = "That isn't the console's password."
NO_PASSWORD = "This console has a password: install it from Get Carts, which asks for it."
CACHE_DIR = "pkg"
ARCHIVE_RAM_SHARE = 4         # an external archive kept in RAM below this share of the largest block

# The names the C shell answers to before the PATH (native/moy_sh): dash's
# built-ins, the C core's and the role commands'. tests/test_pkg.py holds
# them to the C tables and to sh_roles.
SHELL_NAMES = (
    ".", ":", "[", "alias", "basename", "bg", "break", "cat", "cd", "chdir", "clear",
    "command", "continue", "cp", "date", "df", "dirname", "dmesg", "echo", "edit", "eval", "exec",
    "exit", "export", "false", "fg", "free", "getopts", "grep", "hash", "head",
    "help", "jobs", "kill", "local", "log", "ls", "mkdir", "mv", "neofetch", "printf", "ps",
    "pwd", "read", "readonly", "reboot", "return", "rm", "rmdir", "set", "sh",
    "shift", "sleep", "tail", "test", "top", "touch", "trap", "true", "type", "umask",
    "unalias", "uname", "unset", "wait", "wc",
    # the role commands (runtime/shell/sh_roles.py)
    "at", "ble", "cron", "http", "lease", "lora", "media", "mic", "midi", "notify",
    "passwd", "perm", "pkg", "python", "service", "settings", "snd", "usb",
)


size_text = _ci.size_text


# -- the checks Get Carts shares ------------------------------------------------------

def fit_why(inst, cart, plan):
    """Why this console could never play `cart` as `plan` installs it, or
    None: a compiled cart with neither a module for this chip nor a
    main.wasm, one the engine reports it has no room to load, or one larger
    than this board's share for the running set."""
    if cart["runtime"] != "wasm":
        return None
    if plan["load_bytes"] is None:
        chip = inst.chip()[0]
        return "It isn't built for this console's chip%s." % (
            " (%s)" % chip if chip else "")
    if not cart.get("memory"):
        return None
    fit = inst.fit("wasm", cart["memory"], plan["load_bytes"], plan["module"] is None)
    if fit is None:
        return None
    need, have = fit
    if need[0] > have[0] or need[1] > have[1]:
        return _fit_notice(cart["name"], need, have)
    share = _board_share()
    if share is not None and need[0] > share:
        return "Too big for this console: it needs %s, the share is %s." % (
            size_text(need[0]), size_text(share))
    return None


def depends_why(carts, cart, have):
    """Why `cart`'s dependencies stand in its way, or None: one no index
    lists, a cycle, or (for a face that installs one cart at a time) one not
    yet on the console."""
    try:
        chain = _ci.needs(carts, cart, have)
    except _ci.InstallError as exc:
        return exc.text
    if len(chain) > 1:
        return "Get %s first." % chain[0]["name"]
    return None


def blocker(inst, cart, plan, free, runs, fit=None):
    """Why `cart` cannot be installed here, or None. `runs` is whether this
    console carries its runtime; `fit` a `fit_why` answer already made."""
    if not inst.writable() or inst.root() is None:
        return "This console has nowhere to keep carts."
    newer = _ci.newer_why(cart)
    if newer is not None:
        return newer
    if not runs:
        return "This console can't play this kind of cart."
    if _ci.is_revoked(cart):
        return _ci.REVOKED
    if fit:
        return fit
    if free is not None:
        return _ci.room_why(_ci.need_bytes(plan, free[1]), free[0])
    return None


def runs_here(inst, cart):
    return _ci.runs_on(cart, inst.runtimes())


def installed_ids(shelf):
    """The ids of the carts on the shelf `shelf` (the scanned carts)."""
    return set(c.get("id") for c in shelf if c.get("id"))


def _command_name(cid):
    i = cid.find(".")
    return cid[i + 1:] if i > 0 else None


def origin_of(entry):
    """Where an install of index entry `entry` comes from, as the install
    prompt reads it (app_perms.prompt's `origin`), or None."""
    if not entry:
        return None
    return {"publisher": _ci.publisher(entry), "reviewed": _ci.is_reviewed(entry),
            "index": entry.get("index") or ""}


def install_check(inst, stage, folder, shelf, entry=None):
    """The install prompt's check for the build at `stage` (cart_index.
    Install's `ask`), `shelf` the scanned carts, `entry` the index entry it
    installs: `(app id, permissions, prompt lines or None, unreviewed)`;
    lines None for a built-in, which only the console's own release index
    installs; `unreviewed` the index url whose unreviewed warning the lines
    carry (or that the record accepted), else None -- what the consent
    record keeps. An InstallError when a permission it requires is one this
    console lacks, or when it is a command whose name is taken."""
    try:
        man = json.loads(_read(stage + "/manifest.json"))
    except (OSError, ValueError):
        man = {}
    return manifest_check(inst, man, folder, shelf, entry)


def manifest_check(inst, man, folder, shelf, entry=None):
    """`install_check` over the checked build's parsed manifest `man`: what
    a page asks of a console with no screen, whose install is the board's
    and whose manifest the board hands it (web_addon.OfficialCarts)."""
    man = man if isinstance(man, dict) else {}
    cart = _perms.manifest_cart(man, folder, inst.root())
    consent = inst.consent()
    _g, _a, refusal = consent.check(cart, install=True)
    if refusal is not None:
        raise _ci.InstallError(_ci.NOT_HERE, refusal)
    if _perms.vendor(man).get("type") == "command":
        name = _command_name(cart["id"])
        why = None
        if name is None:
            why = "a command needs an id with a dot"
        elif name in SHELL_NAMES:
            why = "the shell's own %s" % name
        else:
            for c in shelf:
                if c.get("type") == "command" and c.get("id") != cart["id"] \
                        and _command_name(c.get("id") or "") == name:
                    why = "%s is %s's command" % (name, c["id"])
        if why is not None:
            raise _ci.InstallError("Its command name is taken: " + why,
                                   "%s: %s" % (folder, why))
    aid = _perms.app_id(cart)
    perms = _perms.perms_of(cart, consent.get(aid))
    if _store.removable(folder):
        return aid, perms, None, None   # a built-in asks nothing, as its seed
    origin = origin_of(entry)
    unreviewed = None
    if origin is not None and not origin["reviewed"] and "python" in _perms.tiers_of(cart):
        unreviewed = origin["index"]
    return aid, perms, _perms.prompt(cart, consent.get(aid), origin), unreviewed


def gated(inst, lines):
    """Whether saying yes to install prompt `lines` takes the console's
    password: they carry the unreviewed warning and the console has one
    (the install role's `locked`)."""
    return _perms.warned(lines) and bool(inst.locked())


# -- the steps it shares ------------------------------------------------------------


def through(steps):
    """`yield from` it: the generator `steps` a resume a frame; closed
    partway (Ctrl-C, the app shut), the rest done at once, so a removal or a
    landing is never left half done."""
    done = False
    try:
        while next(steps, _ci._DONE) is not _ci._DONE:
            yield
        done = True
    finally:
        if not done:
            for _ in steps:
                pass


def removed(inst, folder):
    """`yield from` it, a step a frame: the cart in `folder` taken off the
    shelf with what it wrote (cart_index.remove_steps, a store op a step),
    the consent it was installed with forgotten, its keys deleted
    (app_keys.forget), and the shelf re-read
    there; an optional built-in's removal recorded first, so the seed never
    writes it again, and the browser console's, so the board never fetches it
    again (moy_seed.note_removed). Get Carts' Remove, `pkg remove` and the
    shelf's Delete all step it, through `through`."""
    had = inst.find(folder)
    if _store.recorded(folder):
        inst.op(lambda: _store.note_removed(inst.root(), folder))
    yield from _ci.stepped(inst.op, _ci.remove_steps(inst.root(), folder, inst.keep()))
    if had is not None:
        yield
        aid = _perms.app_id(had)
        yield from inst.consent().forget_steps(aid)
        yield
        inst.op(lambda: _keys.forget(inst.root(), aid))
    yield
    inst.rescan(folder)


def land_now(inst, job, consented):
    """What a face that stops stepping an install calls (Ctrl-C, Get Carts
    closed): a landing begun is finished (`Install.finish`), and so is what
    follows it, `landed`'s consent and re-read, all at once."""
    job.finish()
    if job.path:
        for _ in landed(inst, job.path, consented):
            pass


def landed(inst, path, consented):
    """`yield from` it once an install put its cart at `path`, a step a
    frame: the consent the install prompt was answered with (`(app id,
    permissions)`, or None when it asked nothing) recorded, a built-in's
    removal record dropped (it is the seed's to keep current again) and so is
    the browser console's, then the shelf re-read there. Get Carts and `pkg
    install` both step it."""
    yield from consent_steps(inst, consented)
    folder = path.rsplit("/", 1)[-1]
    if _store.recorded(folder):
        yield
        inst.op(lambda: _store.unremove(inst.root(), [folder]))
    yield
    inst.rescan(path)


def consent_steps(inst, consented):
    """`yield from` it: the install prompt's answer (`(app id,
    permissions, unreviewed)`, install_check's, or None when it asked
    nothing) recorded."""
    if consented is not None:
        yield
        yield from inst.consent().record_steps(
            consented[0], consented[1], consented[2] if len(consented) > 2 else None)


# -- the store's trust ----------------------------------------------------------------


def _mark(inst, folder, cid, on):
    cart = inst.find(folder)
    aid = _perms.app_id(cart if cart is not None else {"id": cid})
    return inst.consent().revoke(aid, on)


def revocations(inst, revoked):
    """The carts this store installed from an index just read whose version
    it revoked, marked in their consent records, and those it no longer
    revokes unmarked: `revoked` is cart_index.parse_index's, {index url:
    {id: [version, ...]}}. Only an index read now speaks, and only for the
    carts installed from it. The folders marked revoked."""
    root = inst.root()
    if not revoked or root is None:
        return []
    rec = inst.op(lambda: _ci.load_record(root))
    out = []
    for folder, entry in rec.items():
        gone = revoked.get(entry.get("index"))
        if gone is None:
            continue
        on = entry.get("version") in (gone.get(entry.get("id")) or ())
        _mark(inst, folder, entry.get("id"), on)
        if on:
            out.append(folder)
    return out


def revoked_rows(inst, rows):
    """`revocations` from rows a console with no screen listed (Get Carts'
    rows over web_addon.listing, each with the version its store holds,
    `had`): the page's own consent records marked to match."""
    out = []
    for r in rows:
        c, had = r["cart"], r.get("had")
        if had is None:
            continue
        on = _ci.is_revoked(c, had)
        _mark(inst, c["folder"], c["id"], on)
        if on:
            out.append(c["folder"])
    return out


def _official_here(root, ident):
    """The folder of the optional built-in or browser console add-on `ident`
    names (its id or its folder) where the store holds it, or None: a seed
    and an add-on the board installed itself have no install record, and
    `pkg remove` takes them as Get Carts does."""
    folder = ident if ident.endswith(".moy") else ident + ".moy"
    if _store.recorded(folder) and _exists(_cart_path(root, folder)):
        return folder
    return None


# -- the verbs ------------------------------------------------------------------------

class Stopped(Exception):
    """Ctrl-C on the face the command runs from."""


STEP_MS = 8                     # an install's work in one step
KEEP_BYTES = 4096               # a cached index's bytes written in one step


class Pkg:
    """The verbs over `inst` (the install role's server, bound, with `carts()`
    the scanned shelf), writing to `out(text)` and `err(line)`. `ask(question)`
    is a yes or no from the user (None: nobody to ask, a no) and
    `password(question)` the line they type, either ASKED where the
    terminal asks it as its prompt (the verb raises Asked, and its line runs
    again with the answer, the build that asked kept for it where `session`
    names the session); `stopped()` is True once Ctrl-C came. The transport is the install role's
    (`inst.net()`: the http worker's where it verifies, cart_index.Worker)."""

    def __init__(self, inst, out, err, ask=None, stopped=None, password=None, session=None):
        self.inst = inst
        self.session = session          # the asking session's (the door's JOB): its build kept (_KEPT)
        self.out = out
        self.err = err
        self.ask = ask
        self.password = password        # (question) -> the line typed, or None
        self.stopped = stopped or (lambda: False)
        self.held = False

    # -- the network ---------------------------------------------------------------

    def _net(self):
        return self.inst.net()

    def _online(self):
        """`yield from` it: the radio held and the link up, asked without
        waiting (the transport's `ready()`; `online()` where it has none)."""
        net = self.inst.net()
        if not self.held:
            if net is None:
                raise _ci.InstallError("This console has no network.")
            self.inst.hold()
            self.held = True
            yield                       # the radio's lease and its dial a frame each
        ready = getattr(net, "ready", None)
        up = ready() if ready is not None else net.online()
        while up is None:
            if self.stopped():
                raise Stopped()
            yield
            up = ready()
        if not up:
            raise _ci.InstallError(_ci.net_text(net, "WiFi isn't connected."))

    def release(self):
        if self.held:
            self.held = False
            self.inst.release()

    def _wait(self, job):
        """`yield from` it: `job` stepped to its end, a step a frame."""
        try:
            while job.step():
                if self.stopped():
                    raise Stopped()
                yield
        except BaseException:
            job.close()
            raise
        return job.data

    # -- the cache --------------------------------------------------------------------

    def _cache_path(self, url):
        key = hashlib.sha256(url.encode()).digest()
        return "%s/%s.json" % (_sibling_path(self.inst.root(), CACHE_DIR),
                               "".join("%02x" % b for b in key[:8]))

    def _cached(self, url):
        path = self._cache_path(url)

        def read():
            if not _exists(path):
                return None
            with open(path, "rb") as f:
                return f.read()
        return self.inst.op(read)

    def _keep(self, url, data):
        """`yield from` it: `data` cached for `url`, KEEP_BYTES a step (a
        card's write runs near 140 KB/s), beside it until whole, then put
        in its place."""
        path = self._cache_path(url)
        part = path + ".part"
        f = []

        def start():
            _mkdir(_sibling_path(self.inst.root(), CACHE_DIR))
            f.append(open(part, "wb"))
        self.inst.op(start)
        done = False
        try:
            for at in range(0, len(data), KEEP_BYTES):
                yield
                self.inst.op(lambda: f[0].write(data[at:at + KEEP_BYTES]))
            done = True
        finally:
            def end():
                f[0].close()
                if done:
                    os.rename(part, path)
                else:
                    _remove(part)
            self.inst.op(end)

    def sources(self):
        return self.inst.op(lambda: _ci.load_indexes(self.inst.root(), self.inst.own()))

    def _fetch(self, url):
        """`yield from` it: `url`'s index, fetched, checked and cached: its
        carts."""
        yield from self._online()
        data = yield from self._wait(_ci.index_fetch(self._net(), url))
        yield
        revoked = {}
        carts = _ci.fitting(_ci.parse_index(data, url, self.inst.own(), revoked))
        yield
        for folder in revocations(self.inst, revoked):
            self.err("%s: its store took it back; it won't run until it is "
                     "removed or updated" % folder)
        yield from self._keep(url, data)
        return carts

    def carts(self, fetch=True):
        """`yield from` it: every cart the sources' indexes list, from the
        cache (a source it does not hold yet fetched when `fetch`)."""
        out = []
        for url in self.sources():
            yield                       # each source's read and its parse a frame apart
            data = self._cached(url)
            yield
            try:
                if data is not None:
                    out.extend(_ci.fitting(_ci.parse_index(data, url, self.inst.own())))
                elif fetch:
                    out.extend((yield from self._fetch(url)))
            except _ci.InstallError as exc:
                self.err("%s: %s" % (url, exc.text))
        return out

    # -- where a cart stands ------------------------------------------------------------

    def _rows(self, carts):
        """[(cart, plan, state)] for `carts` on this console."""
        inst = self.inst
        root = inst.root()
        chip, fmt = inst.chip()
        ranges = bool(getattr(inst.net(), "ranges", False))

        def scan():
            rec = _ci.load_record(root)
            out = []
            for c in carts:
                p = _ci.plan(c, chip, fmt, ranges)
                folder = "%s/%s" % (root, c["folder"])
                present = _exists(folder)
                entry = rec.get(c["folder"])
                man = None
                if present and (entry is None or entry.get("id") != c["id"]
                                or _ci.is_official(c)):
                    try:
                        man = json.loads(_read(folder + "/manifest.json"))
                    except (OSError, ValueError):
                        man = None
                out.append((c, p, _ci.cart_state(c, p, entry, present, man)))
            return out
        return inst.op(scan)

    def _find(self, carts, ident):
        for c in carts:
            if c["id"] == ident or c["folder"] == ident:
                return c
        return None

    # -- the verbs ----------------------------------------------------------------------

    def update(self):
        bad = 0
        for url in self.sources():
            try:
                carts = yield from self._fetch(url)
                self.out("%s: %d carts%s\n" % (url, len(carts),
                                               ", signed" if carts and carts[0]["signed"]
                                               else ""))
            except _ci.InstallError as exc:
                self.err("%s: %s" % (url, exc.text))
                bad += 1
        return 1 if bad else 0

    def list(self, words=()):
        carts = yield from self.carts()
        words = [w.lower() for w in words]
        n = 0
        for c, _p, state in self._rows(carts):
            if words:
                hay = " ".join([c["id"], c["name"], c.get("description") or ""]
                               + list(c.get("tags") or ())).lower()
                if not all(w in hay for w in words):
                    continue
            n += 1
            self.out("%s %d %s %s\n" % (c["id"], c["version"],
                                        "-" if state == "get" else state, c["name"]))
        return 0 if n or not words else 1

    def install(self, idents, yes=False, carts=None):
        if carts is None:
            carts = yield from self.carts()
        have = installed_ids(self.inst.carts())
        todo = []
        for ident in idents:
            c = self._find(carts, ident)
            if c is None:
                self.err("%s: no cart shelf lists it" % ident)
                return 1
            try:
                chain = _ci.needs(carts, c, have)
            except _ci.InstallError as exc:
                self.err("%s: %s" % (ident, exc.text))
                return 1
            for d in chain:
                if d not in todo:
                    todo.append(d)
        yield
        rows = dict((r[0]["id"], r) for r in self._rows(todo))
        yield
        free = self.inst.free()
        for c in todo:
            yield
            _c, p, state = rows[c["id"]]
            if state == "installed":
                self.out("%s %d is installed\n" % (c["id"], c["version"]))
                continue
            if state == "taken":
                self.err("%s: another cart has its folder %s" % (c["id"], c["folder"]))
                return 1
            why = blocker(self.inst, c, p, free, runs_here(self.inst, c),
                          fit_why(self.inst, c, p))
            if why is not None:
                self.err("%s: %s" % (c["id"], why))
                return 1
            code = yield from self._one(c, p, yes)
            if code:
                return code
            free = self.inst.free()
        return 0

    def _licences(self, c, p, yes):
        """The external files whose licence the user accepted: all of them,
        or None when one was not."""
        accepted = []
        for e in p["external"]:
            ref = e["licence"]
            yield from self._online()
            text = _ci.as_text((yield from self._wait(_ci.licence_fetch(self._net(), c, ref))))
            self.out("%s needs %s, under this licence:\n\n%s\n" % (
                c["name"], e["path"], text.rstrip("\n")))
            ok = yes or (self.ask is not None and self.ask("Agree?"))
            if ok is ASKED:
                raise Asked()
            if not ok:
                return None
            accepted.append(e["path"])
        return accepted

    def _one(self, c, p, yes):
        key = json.dumps(c)
        kept = _KEPT.pop(self.session, None) if self.session is not None else None
        if kept is not None and kept[0] != key:
            _drop(kept[1])
            kept = None
        if kept is not None:
            _key, job, consented = kept
            for _e in p["external"]:    # the licences' answers, given before the download
                ok = yes or (self.ask is not None and self.ask("Agree?"))
                if ok is ASKED:
                    _KEPT[self.session] = kept
                    raise Asked()
                if not ok:
                    _drop(job)
                    self.err("%s: %s" % (c["id"], _ci.NO_LICENCE))
                    return 1
        else:
            job, consented = yield from self._start(c, p, yes)
            if job is None:
                return 1
        stepped_through = False
        try:
            while job.step(STEP_MS):
                yield
                if job.asking is not None:
                    self.release()
                    if kept is None:
                        self.out("".join(ln + "\n" for ln in job.asking))
                    why = self._gate(job.asking)
                    ok = why is None and (yes or (self.ask is not None and self.ask("Install?")))
                    if why is ASKED or ok is ASKED:
                        if self.session is not None:
                            _KEPT[self.session] = (key, job, consented)
                            stepped_through = True
                        else:
                            _drop(job)
                        raise Asked()
                    if why is not None:
                        job.cancel()
                        self.err("%s: %s" % (c["id"], why))
                    elif ok:
                        job.agree()
                    else:
                        job.cancel()
                elif self.stopped() and job.cancel():
                    raise Stopped()
            stepped_through = True
        finally:
            if not stepped_through:
                land_now(self.inst, job, consented[0] if consented else None)
            self.release()
        if not job.path:
            self.err("%s: %s" % (c["id"], job.error or "Not installed. Nothing changed."))
            return 1
        yield from through(landed(self.inst, job.path, consented[0] if consented else None))
        self.out("%s %d installed: %s\n" % (c["id"], c["version"], job.report()))
        return 0

    def _start(self, c, p, yes):
        """`yield from` it: cart `c`'s install begun, its licences accepted:
        `(job, consented)`, the list the install prompt's check fills; None
        for the job where a licence was not accepted."""
        accepted = yield from self._licences(c, p, yes)
        if accepted is None:
            self.err("%s: %s" % (c["id"], _ci.NO_LICENCE))
            return None, None
        yield from self._online()
        arc = 0
        for e in p["external"]:
            arc += e["archive"]["size"]
        mem = self.inst.memory()
        keep = "store" if (arc and mem is not None
                           and mem[1] < ARCHIVE_RAM_SHARE * arc) else "ram"
        consented = []

        def ask(stage):
            got = install_check(self.inst, stage, c["folder"], self.inst.carts(), c)
            consented[:] = [(got[0], got[1], got[3])]
            return got[2]

        job = _ci.Install(c, p, self.inst.net(), self.inst.root(), self.inst.op,
                          accepted, archive=keep, keep=self.inst.keep(), ask=ask)
        self.out("Getting %s %d (%s)\n" % (c["id"], c["version"],
                                            size_text(p["download_bytes"])))
        return job, consented

    def _gate(self, lines):
        """None when the prompt `lines` may be answered; else why not: the
        console's password, asked for an unreviewed Python cart (`gated`),
        not typed right, or nobody here to type it; ASKED where the terminal
        asks for it."""
        if not gated(self.inst, lines):
            return None
        if self.password is None:
            return NO_PASSWORD
        typed = self.password("The console's password:")
        if typed is ASKED:
            return ASKED
        return None if typed and self.inst.unlock(typed) else WRONG_PASSWORD

    def remove(self, idents):
        inst = self.inst
        code = 0
        for ident in idents:
            folder = inst.op(lambda: _ci.find_installed(inst.root(), ident)
                             or _official_here(inst.root(), ident))
            if folder is None:
                self.err("%s: %s" % (ident, "a core cart, which a console keeps"
                                     if _store.is_core(ident) else "not installed by pkg"))
                code = 1
                continue
            yield from through(removed(inst, folder))
            self.out("%s removed\n" % ident)
        return code

    def upgrade(self, yes=False):
        carts = yield from self.carts()
        ids = [c["id"] for c, _p, state in self._rows(carts) if state == "update"]
        if not ids:
            self.out("Nothing to upgrade.\n")
            return 0
        return (yield from self.install(ids, yes, carts))

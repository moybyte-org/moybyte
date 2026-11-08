"""The console's end of the achievements process (docs/os_2026-11/processes.md
section 8, docs/app_api_v1.md section 9): `ws.ach`, the notify role's
server, the shell's own badges and the readers both ends share.

A game declares its badges in its manifest, `"moybyte": {"achievements":
[{"id", "title", "icon", "hint", "goal"?, "secret"?}, ...]}` (`declared`),
and unlocks them with messages to the process (system_carts/
moybyte.achievements.moy, PROC): `award(id)` and `progress(id, value)`, which
`moybyte.award` and `moybyte.progress` send. The OS stamps the sender, so a
game reaches only the badges its own manifest declares. The console's own
badges (`SHELL`) are the `"system"` sender's: the shell tracks its milestones
(carts played, editors used) in its own row of `system/settings`
(`MILESTONES`) and sends `award` and `progress` like any game.

The process keeps what is found, and each goal's last value, in its own
prefs (`PREFS_KEY`, read strictly by `read_badges`): one record a sender,
`{"name": its name, "found": {id: [day, title, icon, hint]}, "value": {id:
n}}`, so a deleted game's badges stay listed under its name. The console
reads that row for Settings (`groups`), and shows the process's notification
of a fresh find as the unlock banner (`badge_of`).

The console starts the process when it loads its store (`Link.start`),
handing it (`Link.env`): `game(app)`, the app's name and the badges it
declares (None for an app that declares none), `clock_day()`, today as
YYYYMMDD off the bar's clock (0 without one), and `row_stamp()`, which
moves when the row's text does, so the process can hold the row in memory
and read it again only after a write that is not its own (`seed`, a sync).
"""

try:
    import scheduler as _sched
except ImportError:  # host: the runtime package
    from runtime import scheduler as _sched
try:
    import app_perms as _perms
except ImportError:  # host: the runtime package
    from runtime import app_perms as _perms
try:
    import json as _json
except ImportError:  # pragma: no cover
    import ujson as _json

PROC = "moybyte.achievements"           # the cart the notify role's rows name
TITLE = "Achievements"
SYSTEM = "system"                       # the console's own sender
SHELL_NAME = "Moybyte"                  # its badges' group on the page
PREFS_KEY = "badges"                    # the process's prefs row
MILESTONES = "milestones"               # the shell's own row of system/settings
ID_MAX = 32                             # a badge id's bytes

HEAD_BADGE = "You found a badge!"       # the shell's own banner heads
HEAD_SECRET = "You found a secret!"
_GAME_BADGE = ": badge found!"          # a game's: its name, then this
_GAME_SECRET = ": secret found!"

# The console's own badges, declared as a game declares its own. A hidden
# one's find is an Easter egg (runtime/system/achievements_ui.py); play_five
# and toolbox are the shell's counted milestones (`Link.played`, `Link.used`).
SHELL = (
    {"id": "first_open", "title": "First Steps", "icon": "app", "hint": "Open a cart"},
    {"id": "first_run", "title": "Lift Off!", "icon": "run", "hint": "Run a game"},
    {"id": "first_paint", "title": "Little Artist", "icon": "paint", "hint": "Save a drawing"},
    {"id": "first_map", "title": "Map Maker", "icon": "map", "hint": "Save a map"},
    {"id": "first_code", "title": "Code Wizard", "icon": "code", "hint": "Save some code"},
    {"id": "play_five", "title": "Cart Explorer", "icon": "star", "hint": "Play 5 carts",
     "goal": 5},
    {"id": "toolbox", "title": "Toolbox Master", "icon": "settings", "hint": "Use 3 editors",
     "goal": 3},
    {"id": "decorator", "title": "Home Decorator", "icon": "heart", "hint": "Pick a wallpaper"},
    {"id": "konami", "title": "Secret Coder", "icon": "spark", "hint": "Type the secret code",
     "secret": True},
    {"id": "clock_tinker", "title": "Time Traveler", "icon": "smile",
     "hint": "Tap the clock 7 times", "secret": True},
    {"id": "secret_door", "title": "Secret Finder", "icon": "key",
     "hint": "Knock on Settings 5 times", "secret": True},
)
TOOLBOX = ("code", "paint", "map")      # the editors Toolbox Master counts

_FIELDS = ("id", "title", "icon", "hint", "goal", "secret")
_TEXT = ("id", "title", "icon", "hint")


# -- the readers -------------------------------------------------------------

def declared(raw):
    """The badges a manifest's `achievements` field declares, in its order,
    each `{"id", "title", "icon", "hint", "goal", "secret"}` (`goal` None
    where it has none, `secret` False where it says nothing); None where the
    field is absent or does not read. Strict: a list of objects holding no
    other key, each text field non-empty text, an id at most ID_MAX bytes
    and unique, a goal a whole number of 1 or more, `secret` true or false;
    one entry that breaks a rule and the field reads as absent."""
    if type(raw) is not list or not raw:
        return None
    out = []
    ids = {}
    for a in raw:
        if type(a) is not dict:
            return None
        for k in a:
            if k not in _FIELDS:
                return None
        for k in _TEXT:
            v = a.get(k)
            if type(v) is not str or not v:
                return None
        i = a["id"]
        if len(i.encode()) > ID_MAX or i in ids:
            return None
        goal = a.get("goal")
        if goal is not None and (type(goal) is not int or goal < 1):
            return None
        secret = a.get("secret", False)
        if type(secret) is not bool:
            return None
        ids[i] = True
        out.append({"id": i, "title": a["title"], "icon": a["icon"], "hint": a["hint"],
                    "goal": goal, "secret": secret})
    return out


def _found_ok(f):
    return (type(f) is list and len(f) == 4 and type(f[0]) is int
            and type(f[1]) is str and type(f[2]) is str and type(f[3]) is str)


def read_badges(doc):
    """The process's prefs row as `{sender: {"name", "found", "value"}}`
    (`found` {id: [day, title, icon, hint]}, `value` {id: n}, each {} where
    the row leaves it out), or None where it is absent or does not read:
    strict, one record that breaks the shape and the whole row reads as
    absent."""
    if type(doc) is not dict:
        return None
    out = {}
    for app, rec in doc.items():
        if type(app) is not str or type(rec) is not dict:
            return None
        name = rec.get("name")
        found = rec.get("found", {})
        value = rec.get("value", {})
        if type(name) is not str or type(found) is not dict or type(value) is not dict:
            return None
        for k in rec:
            if k not in ("name", "found", "value"):
                return None
        for i, f in found.items():
            if not _found_ok(f):
                return None
        for i, v in value.items():
            if type(v) is not int:
                return None
        out[app] = {"name": name, "found": found, "value": value}
    return out


def head(sender, name, secret):
    """The unlock banner's head for a find of `sender`'s (`name` its name):
    the shell's own say who found it, a game's name the game."""
    if sender == SYSTEM:
        return HEAD_SECRET if secret else HEAD_BADGE
    return name + (_GAME_SECRET if secret else _GAME_BADGE)


def is_secret(text):
    """Whether banner head `text` announces a secret's find."""
    return text == HEAD_SECRET or text.endswith(_GAME_SECRET)


def is_proc(cart):
    """Whether `cart` is the achievements process's: by its id, or, for an
    embedded seed entry that carries none, by its title and type."""
    cid = cart.get("id")
    if cid is not None:
        return cid == PROC
    return cart.get("type") == "service" and cart.get("title") == TITLE


def end():
    """Stop the running achievements process, whichever console started it.
    A VM has one, as a board has one console: a new console's Link ends the
    last one's (its `game` and `clock_day` read that console), and its own
    `start` runs it again."""
    sched = _sched.SCHED
    for name in (PROC, TITLE):
        while True:
            old = sched.find(name)
            if old is None or old.cart is None or not is_proc(old.cart):
                break
            if not sched.stop(old.pid):
                break


class Link:
    """`ws.ach`: the achievements process as the console reaches it."""

    def __init__(self, ws):
        end()
        self.ws = ws
        self.pid = None
        self.key = PROC + "_" + PREFS_KEY   # the process's row of system/settings
        # The unlock banner's payload, (head, title, icon, secret), set when
        # the process's notification arrives; the banner's deadline is the
        # console's own flat field (`ws._toast_until`).
        self.toast = None
        self.mute = False       # the shell's own sends dropped (a one-cart player)
        self._rows_gen = -1     # the rows' generation `_doc` was read at
        self._text = None       # the row's text it was decoded from
        self._doc = {}
        self._doc_n = 0         # moves with every decode
        self._made = (-1, -1, -1)
        self._groups = ()
        self._found = 0
        self._total = 0
        self._games = {}        # app -> game(app)'s answer, at the shelf's `_games_at`
        self._games_at = (-1, -1)

    # -- the process --------------------------------------------------------

    def _cart(self):
        carts = getattr(self.ws, "carts", None)
        for c in (carts.all if carts is not None else ()):
            if is_proc(c):
                return c
        return None

    def start(self):
        """(Re)start the process: a running one is stopped first (`end`).
        Answers its pid, or None."""
        end()
        cart = self._cart()
        if cart is not None:
            self.key = _perms.app_id(cart) + "_" + PREFS_KEY
        self.pid = None if cart is None else _sched.start_cart(self.ws, cart)
        return self.pid

    def env(self):
        """What the console hands the process, in its namespace."""
        return {"game": self.game, "clock_day": self.ws._today_ymd,
                "row_stamp": self.stamp}

    def game(self, app):
        """App `app`'s name and declared badges, `{"name", "achievements"}`,
        or None for an app that declares none: read once a sender while the
        shelf's roster and titles stand (`carts.gen`, `carts.titled`: a
        rescan, an install, a delete or a rename reads it afresh)."""
        carts = getattr(self.ws, "carts", None)
        at = (carts.gen, carts.titled) if carts is not None else (0, 0)
        if at != self._games_at:
            self._games = {}
            self._games_at = at
        if app in self._games:
            return self._games[app]
        got = None
        if app == SYSTEM:
            got = {"name": SHELL_NAME, "achievements": declared(list(SHELL))}
        for c in (carts.all if carts is not None and app != SYSTEM else ()):
            if not is_proc(c) and _perms.app_id(c) == app:
                decl = declared(c.get("achievements"))
                if decl is not None:
                    got = {"name": c.get("title") or app, "achievements": decl}
                break
        self._games[app] = got
        return got

    def stamp(self):
        """Which text of the process's row stands: a number that moves only
        when the row's text does. The process keeps the row in memory and
        reads it again only where the stamp moved past its own last write
        (`seed`, a restore)."""
        self.doc()
        return self._doc_n

    def _proc(self):
        pid = self.pid
        return None if pid is None else _sched.SCHED.procs.get(pid)

    def running(self):
        p = self._proc()
        return p is not None and p.state != _sched.EXITED

    def badge_of(self, n):
        """The unlock banner notification `n` announces, (head, title,
        icon, secret), or None where the process did not post it."""
        if n is None or self.pid is None or n.pid != self.pid:
            return None
        return (n.summary, n.body, n.icon, is_secret(n.summary))

    # -- the shell's own badges: messages from the system ----------------------

    def award(self, ach_id):
        """The shell's badge `ach_id` to the process: True where it is not
        found yet and the process took the message."""
        if self.mute or self.pid is None or self.has(SYSTEM, ach_id):
            return False
        return _sched.SCHED.send(self.pid, "award", (ach_id,))

    def progress(self, ach_id, value):
        """The shell's counted badge `ach_id` stands at `value`."""
        if self.mute or self.pid is None or self.has(SYSTEM, ach_id):
            return False
        return _sched.SCHED.send(self.pid, "progress", (ach_id, value))

    def _milestone(self, kind, key, ach_id):
        """`key` joins the shell's `kind` milestone; a new one reports
        `ach_id`'s count. Kept until the count reaches its goal."""
        goal = 0
        for a in SHELL:
            if a["id"] == ach_id:
                goal = a["goal"]
        if self.mute or key is None or self.has(SYSTEM, ach_id):
            return
        rows = self.ws.system
        got = rows.get(MILESTONES, None)
        got = got if type(got) is dict else {}
        keys = got.get(kind)
        keys = keys if type(keys) is list else []
        if key in keys or len(keys) >= goal:
            return
        keys = keys + [key]
        got[kind] = keys
        try:
            rows.set(MILESTONES, got)
        except Exception as exc:  # noqa: BLE001 -- the count is kept in RAM
            print("Moybyte milestones save failed:", "%s: %s" % (type(exc).__name__, exc))
        self.progress(ach_id, len(keys))

    def played(self, key):
        """A cart opened (`key` its path or title): First Steps, and a
        distinct cart for Cart Explorer."""
        self._milestone("played", key, "play_five")
        self.award("first_open")

    def used(self, view):
        """An Editor tab shown: a distinct editor for Toolbox Master."""
        if view in TOOLBOX:
            self._milestone("used", view, "toolbox")

    # -- the reads: the process's row -------------------------------------------

    def doc(self):
        """The process's row, decoded (`read_badges`), {} where it holds none."""
        rows = self.ws.system
        g = rows.gen()
        if g != self._rows_gen:
            self._rows_gen = g
            t = rows.text(self.key)
            if t != self._text:
                self._text = t
                got = None
                if t:
                    try:
                        got = read_badges(_json.loads(t))
                    except ValueError:
                        got = None
                self._doc = got or {}
                self._doc_n += 1
        return self._doc

    def seed(self, found):
        """Put the shell's badges `found` ({id: day}) as the process's
        whole row: what a tool or a test starts from. The process, which
        holds the row in memory, reads it again at its next message
        (`stamp`)."""
        f = {}
        for a in SHELL:
            if a["id"] in found:
                f[a["id"]] = [found[a["id"]], a["title"], a["icon"], a["hint"]]
        self.ws.system.set(self.key, {SYSTEM: {"name": SHELL_NAME, "found": f}} if f else {})

    def has(self, app, ach_id):
        rec = self.doc().get(app)
        return rec is not None and ach_id in rec["found"]

    def groups(self):
        """Settings' list: `((app, name, badges), ...)`, the shell's first,
        then each game on the shelf that declares a badge, then each game
        gone from it whose badges were found. A badge is `(id, title, icon,
        hint, goal, secret, day, value)`, `day` None while it is not found.
        Made again when the row or the shelf moves."""
        self.doc()
        carts = getattr(self.ws, "carts", None)
        cg = carts.gen if carts is not None else 0
        ct = carts.titled if carts is not None else 0
        m = self._made
        if m[0] == self._doc_n and m[1] == cg and m[2] == ct:
            return self._groups
        self._made = (self._doc_n, cg, ct)
        doc = self._doc
        out = [(SYSTEM, SHELL_NAME, _badges(SHELL, doc.get(SYSTEM)))]
        listed = {SYSTEM: True}
        for c in (carts.all if carts is not None else ()):
            if is_proc(c):
                continue
            decl = declared(c.get("achievements"))
            if decl is None:
                continue
            app = _perms.app_id(c)
            if app in listed:
                continue
            listed[app] = True
            out.append((app, c.get("title") or app, _badges(decl, doc.get(app))))
        for app in sorted(doc):
            rec = doc[app]
            if app not in listed and rec["found"]:
                out.append((app, rec["name"], _badges((), rec)))
        self._groups = tuple(out)
        n = t = 0
        for _app, _name, badges in self._groups:
            t += len(badges)
            for b in badges:
                if b[6] is not None:
                    n += 1
        self._found, self._total = n, t
        return self._groups

    def count(self):
        """How many badges are found, of every group's."""
        self.groups()
        return self._found

    def total(self):
        """How many badges the groups list."""
        self.groups()
        return self._total

    def today(self):
        return self.ws._today_ymd()


def _badges(decl, rec):
    """A group's badges: `decl`'s in its order, then any of `rec`'s found
    ones it no longer declares."""
    found = rec["found"] if rec is not None else {}
    value = rec["value"] if rec is not None else {}
    out = []
    seen = {}
    for a in decl:
        i = a["id"]
        seen[i] = True
        f = found.get(i)
        out.append((i, a["title"], a["icon"], a["hint"], a.get("goal"), a.get("secret", False),
                    f[0] if f is not None else None, value.get(i, 0)))
    for i in sorted(found):
        if i not in seen:
            f = found[i]
            out.append((i, f[1], f[2], f[3], None, False, f[0], 0))
    return tuple(out)


class NotifyRole:
    """The notify role's server (native/os_calls.json's `proc:achievements`
    rows): the shell's own badges and Settings' list, through `ws.ach`."""

    def __init__(self, ws):
        self.__ws = ws

    def award(self, g, ach_id):
        """The shell's badge `ach_id` (a hidden one's find, a save's), to
        the process: True where it is new."""
        return bool(self.__ws.ach.award(ach_id))

    def groups(self, g):
        """Settings' list of badges by game (`Link.groups`)."""
        return self.__ws.ach.groups()

    def today(self, g):
        """Today as the badges record it (YYYYMMDD off the bar's clock), 0
        without a clock."""
        return self.__ws.ach.today()

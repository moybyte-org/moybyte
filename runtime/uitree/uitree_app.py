# Map (grep -n a name to jump there):
#   Engine                what every engine shares: state, a handler's run, the page's open and close, roles
#   Engine.run            a handler run, counted (G12)
#   Engine.open           the page built at open, its hook fired
#   Engine.point          a pointer routed through the tree
#   Engine.back           back: the closer's action, else a shown dialog's or popover's
#   App                   a registered app whose screen is a build
#   env                   what the host handed the engine running this handler
#   app_layer             the class a manifest names, made from a build
#   Page                  a build hosted by a surface still drawn with today's verbs
#   use_engine            which engine runs the apps and hosted pages made from now on
#   hosted_page           a hosted page on the engine in use
"""The UI tree's engines' common ground and the app protocol over them:
`Engine` is what the C engine (`uitree_c.CEngine`) and the reference
(`uitree_ref.Engine`, the replay through the toolkit's `ui` verbs) share --
the state, a handler's run and commit, the page's open and close, the roles
and the edges. `App` is a registered app whose screen is a build
(`app_layer` makes the class a manifest names); `Page` is a build hosted by
a surface still drawn with today's verbs; `use_engine` picks the engine both
are made on."""

import sys

import ui as _v
from layers import AppLayer
from layout_base import bar_rows, content_of, metric_class, DESK

import uitree as S
from uitree import _NOW

EDGE_UP, EDGE_DOWN, EDGE_LEFT, EDGE_RIGHT = 1, 2, 4, 8   # moy_ui_api.h's MOY_UI_EDGE_*

_IN_REP = 10                    # the frame input's repeat word, a byte a direction (ui_draw._IN_REP)
ID_MAX = 31            # bytes of a node id the host engine keeps (seen: a longer focus id comes back cut)


class Engine(S.Builder):
    """One app's built tree inside a frame its host has begun
    (`host.begin`), and the commits of its handlers: what both engines
    share; each draws the tree its own way."""

    band = True         # False: the host draws the page's keys (a handheld popover's strip), the page none
    bare = False        # True: the host painted the page's ground (a popover's card), the page paints none

    close_root = None       # what ui.pop() on the first page does: a hosted page's host closes it

    def __init__(self, decl, build, host, surface, damage, files=None, gen=False, env=None):
        self.decl = decl
        self.env = env                  # what the host hands its handlers (`env()`)
        self._gen_on = gen              # the page's gen: off where a golden pins today's draw counts
        self._ui, self._surf, self._damage = host, surface, damage
        self.init_builder(decl.state, build)
        self.q = []
        self.ids = {}                   # a stop's id -> its (node, row), kept across frames
        self._seen = {}                 # a stop's id -> the replay pass that last drew it
        self._pass = 0                  # the replay passes so far
        self._n_ids = 0                 # the ids this pass drew
        self._list_at = {}              # a cards list's key -> [the pass that last drew it, the one before]
        self._vr = {}                   # a long rows list's key -> [its key, unit, row heights] (_vrows)
        self._all_names = None          # every state name, and whether one is a document (no versions)
        self._has_doc = False
        self._live, self._live_at, self._live_cls, self._live_in = None, -2, None, None
        self._rf = {}                   # a node kind -> its replay function
        self._foot_btns = []            # the foot's shown buttons, refilled each frame
        self._order_of = {}             # a list's key -> the model its row order was taken from
        self._no_stops = -2             # the states' versions at which the page drew no stop
        self._hint = None               # the band's hints, and what they were made at (_hints)
        self._hk = None
        self._hk_v = self._hk_n = self._hk_a = -2
        self._hk_c = self._hk_tv = self._hk_d = self._hk_f = self._hk_at = None
        self.dups = set()
        self._sync = {}
        self._orders = {}               # a list's key -> the order of its rows (_after_removal)
        self._pid = (None, 0, "")       # (build, level, the id _page_id made for them)
        self.curs = []
        self.tv_stop = False
        self.fresh = False
        self._new_build = False         # no pass has drawn since the page was built (_land_focus)
        self.focused = None
        self.drawn = {}
        self.actions = decl.actions
        self._in_pop = False            # a popover's children replaying
        self._roles = None              # role -> its action, made again when an action is declared
        self._roles_n = -1
        self.files = files
        self.pics = None                # the picture engine, made at the first frame over its canvas
        self.calls = 0                  # handler runs this frame: actions, hooks, timers, posts, work steps
        self.frame_calls = []
        self.work_run = {}              # work name -> [first frame, frames, steps, ms, over, wrote]
        self.frame_no = 0
        self.pic_wh = {}
        self.drafts = {}                # a commit field's key -> its text until it is written
        self._page_o = {"gen": 0}       # the page's gen: moves when what its rows show may have
        self._gen_ok = False
        self._page_names = ()           # the states the page's gen counts: all but its cells' own
        self._gen_vals = 0              # the engine values its text names (`_engine_values`)
        self._gv = 0                    # what they are this frame, hashed
        self._cells = {}                # a split's region (its group) -> [its override block, the states it reads]
        self._split_w = {}              # a split -> [its versions, its span, its widths]; False: no link in it
        self._edits = 0                 # a commit field's draft edits, which move the gen too
        self._buttons = 0               # the frame's buttons, as keys() handed them to the host
        st = decl.state                 # this engine's own values: two consoles in one process each hold theirs
        self._own = [dict((k, st._init.get(k)) for k in st._t), dict.fromkeys(st._t, 0)]

    def _bind(self):
        """The module's state holds this engine's values: at every way in (a
        frame, a handler, input, an opening, a way out) an engine that is not
        the last one in puts the other's values by and takes its own. The
        first engine in takes the module's as they are. The dicts stay the
        objects templates and derives read. The state names its holder by
        the engine's [values, versions] pair, never the engine: a module
        outlives its consoles."""
        st = self.s
        was, own = st._owner, self._own
        if was is own:
            return
        if was is not None:
            was[0], was[1] = dict(st._v), dict(st._ver)
            st._v.clear()
            st._v.update(own[0])
            st._ver.clear()
            st._ver.update(own[1])
        object.__setattr__(st, "_owner", own)

    # -- the engine's counted runs (G12) ------------------------------------

    def run(self, fn, *args, action=None):
        self._bind()
        if action != "setup":
            self.calls += 1
        return S.Builder.run(self, fn, *args, action=action)

    @property
    def idle_owed(self):
        return (self.pics is not None and self.pics.idle_owed) or self.legacy_moved()

    def legacy_moved(self):
        """Whether a hosted surface's gen moved since it drew: this replay
        hosts none."""
        return False

    def idle_step(self):
        """A frame with nothing to paint: one owed picture decoded, the card
        showing it from the next frame."""
        if self.pics is not None and self.pics.idle_step():
            self._damage.all()

    def _posts(self):
        if not self.decl.posts:
            return
        q, self.decl.posts[:] = list(self.decl.posts), []
        tk = self.s._tickets
        for act, arg, ticket in q:
            if ticket is not None and tk.get(ticket.load) != ticket.n:
                was, S._NOW.app = S._NOW.app, self
                S._trace("post", act.name, "dropped: %r is stale (%s is at #%d)" % (
                    ticket, ticket.load, tk.get(ticket.load, 0)))
                S._NOW.app = was
                continue
            self.run_action(act, (arg,) if act.takes == "arg" else (), "post")

    def _works(self):
        """Each work whose due= holds: one step a frame, after the frame's
        input and paint; a step past its budget is counted. One trace line
        per run of frames, not per step."""
        works = self.decl.works
        if not works:
            return                      # a view of an empty dict is still made on a board
        import time
        for name in works:
            w = works[name]
            if w.unit is not None and S.LIVE is not None and w.unit not in S.LIVE:
                continue
            S._NOW.app = self
            try:
                due = w.due(None)
            finally:
                S._NOW.app = None
            run = self.work_run.get(w.name)
            if not due:
                if run is not None:
                    was, S._NOW.app = S._NOW.app, self
                    S._trace("work", w.name, "ended: %d steps over %d frames, %.1f ms, %d over budget; "
                             "wrote %s" % (run[2], run[1], run[3], run[4], ", ".join(sorted(run[5])) or "nothing"))
                    S._NOW.app = was
                    del self.work_run[w.name]
                continue
            if run is None:
                run = self.work_run[w.name] = [self.frame_no, 0, 0, 0.0, 0, set()]
                was, S._NOW.app = S._NOW.app, self
                S._trace("work", w.name, "due (%s), budget %d ms a frame" % (w.due_src, w.budget))
                S._NOW.app = was
            run[1] += 1
            t0 = time.perf_counter()
            vers = dict(self.s._ver)
            S._TRACE_OFF[0] = True          # a step's writes are counted in its run's line
            try:
                self.run(w.fn, action="work " + w.name)
                S.commit(self)
            finally:
                S._TRACE_OFF[0] = False
            run[2] += 1
            ms = (time.perf_counter() - t0) * 1000
            if ms > w.budget:
                run[4] += 1
            run[3] += ms
            self._wrote(run[5], vers)

    def _wrote(self, names, vers):
        """Add to `names` the states whose versions moved past `vers`."""
        for k, v in self.s._ver.items():
            if vers.get(k) != v:
                names.add(k)

    def open(self, arg=None, hook="opened"):
        """The app opens: its first page, and its `opened` hook (or the hook
        a hosted page names) run once, handed the launch argument when it
        takes=\"arg\"."""
        self._bind()
        self.pages = self.pages[:1]
        self.openers = []
        self._ui.reset()
        self.root = None
        self.want_focus = None          # a fresh visit: no focus, cur or row order carried over
        self._sync, self.ids, self.focused, self.drawn = {}, {}, None, {}
        self._vr = {}
        self._seen = {}
        self.drafts = {}
        self._orders = {}
        self._list_at = {}
        hook = self.actions.get(hook)
        if hook is not None and hook.hook:
            self.run(hook.fn, *((arg,) if hook.takes == "arg" else ()), action="opened")
        S.commit(self)

    def close(self):
        """The visit is over (its host left the page): the built tree is let
        go, built again at the next frame; the next open resets the rest."""
        self.root = None
        self._vr = {}

    def forget(self):
        """Everything the page holds for a visit let go (the C engine's store
        too): as `close` here."""
        self.close()

    _started = False        # the frame's start ran in its hosted phase

    # -- the frame --------------------------------------------------------

    def _start(self, dt):
        """The frame's start: its posts and the timers due, before anything
        is placed."""
        self.frame_no += 1
        self.calls = 0
        self._posts()
        self.clock += int(dt * 1000)
        if self.timers:
            self._timers_due()

    def _timers_due(self):
        for t in [t for t in self.timers if t[0] <= self.clock]:
            self.timers.remove(t)
            self.run_action(t[1], (), "timer")

    def hosted(self, dt, cv, th, content, desk):
        """The frame's first phase, which a host runs BEFORE it begins the
        frame (`host.begin`): the surfaces the page hosts (`legacy`) drawn
        each in its own verbs pass. This replay's layout is the toolkit's,
        which places nothing before the pass: it hosts none (`_r_legacy`)."""

    GEN_UNSAFE = ("canvas", "picture", "cards", "text_editor", "legacy")

    @staticmethod
    def _images(n):
        """Whether `n` (cards or a picture) draws an image, decoded late."""
        return "image" in n.p or any("image" in k.p for k in n.kids)

    def _page_id(self):
        """The toolkit's id of the page on top: its build's name and its
        level in the stack, so a title that moves keeps the page's focus and
        scroll, and two levels sharing a title keep their own."""
        b, n = self.pages[-1], len(self.pages)
        m = self._pid
        if m[0] is not b or m[1] != n:
            m = self._pid = (b, n, ("%d %s" % (n, getattr(b, "__name__", "page")))[:ID_MAX])
        return m[2]

    STOPS = ("link", "button", "toggle", "slider", "field", "seg", "item", "card", "tabs")

    def role_action(self, role):
        """The declared action answering `role:ROLE` (one per key, K02).
        Asked every frame for the arrows: a lookup, no string made."""
        roles = self._roles
        if roles is None or self._roles_n != len(self.actions):
            roles = {}
            for a in self.actions.values():
                for k in a.keys:
                    if k.startswith("role:") and k[5:] not in roles:
                        roles[k[5:]] = a
            self._roles, self._roles_n = roles, len(self.actions)
        return roles.get(role)

    def role(self, role):
        """A host that routes its own keys (kernel chrome) presses ROLE: the
        action answering it runs now, as one commit, when it is on offer.
        True when one ran."""
        a = self.role_action(role)
        if a is None or not a.can():
            return False
        self.run_action(a, self.args_for(a), "role %s" % role)
        self._damage.all()
        return True

    def _id(self, nid, pair):
        """Register this frame's stop `nid` (its (node, row) `pair`): the ids
        dict is kept across frames, each id stamped with the pass it was
        drawn in; two nodes under one id in one pass is a fragment used twice."""
        ids, seen, p = self.ids, self._seen, self._pass
        got = ids.get(nid)
        if seen.get(nid) == p:
            if got is not None and got[0] is not pair[0]:
                self.dups.add(nid)
        else:
            seen[nid] = p
            self._n_ids += 1
        if got is not pair:
            ids[nid] = pair

    # -- input --------------------------------------------------------------

    def _arrow_roles(self, i):
        """An arrow whose role an action answers (§13.1: left, right, up,
        down) fires that action on its press and on each repeat, and the
        toolkit never sees it: the app owns the arrow, focus stays."""
        inp, fired = self._ui.inp, False
        for bit in range(4):
            role = S.ARROWS[bit]
            a = self.role_action(role)
            if a is None:
                continue
            down = inp[0] & (1 << bit)
            reps = (inp[_IN_REP] >> (8 * bit)) & 0xFF
            if (down or reps) and a.can():
                self.q.append((a, self.args_for(a), "role %s" % role))
                fired = True
            if down or reps:
                inp[0] &= ~(1 << bit)
                inp[1] &= ~(1 << bit)
                u = inp[_IN_REP] & 0xFFFFFFFF & ~(0xFF << (8 * bit))
                inp[_IN_REP] = u - 0x100000000 if u >> 31 else u     # the word is signed: no bigint
        return fired

    def point(self, px, py, click):
        self._bind()
        if self._ui.point(px, py, click):
            self._damage.all()
        return True

    def has_page(self):
        was, _NOW.app = _NOW.app, self      # a size class (@desk) reads the app's
        try:
            return self._has_page()
        finally:
            _NOW.app = was

    # -- the keys between surfaces: one engine's page hands them to another's

    def edge(self):
        """Which way the last frame's step found no stop past the focused
        one, outside a dialog (EDGE_* bits, 0 none): the keys went past this
        page's side and its focus stayed. Whoever holds the keys above the
        pages hands them on that way (`enter`)."""
        return _v._C.api_edge(self._ui.ctx)

    def enter(self, way):
        """The keys come into this page travelling `way` (EDGE_*): its next
        frame drawn with them lands focus on its stop nearest the side they
        come in by (its first going down, its last going up, its rightmost
        going left, its leftmost going right)."""
        _v._C.api_enter(self._ui.ctx, way)
        if self._damage is not None:
            self._damage.all()

    def back(self):
        was, _NOW.app = _NOW.app, self      # a size class (@desk) reads the app's
        try:
            return self._back()
        finally:
            _NOW.app = was

    def trace_text(self):
        return S.dotted("\n".join(self.trace), lead=True)


class App(AppLayer):
    """A registered app whose screen is a `uitree` build: the app protocol
    (docs/app_api_v1.md) over an `Engine`. `app_layer` makes the class the
    app's manifest entry names."""

    NEEDS = ("surface", "theme", "damage")
    UI = True
    domain = "system"
    BUILD = None            # (build,): the app module's root page
    IS_APP = None           # (is_app,): claims the app's identity cart
    ENGINE = None           # (Engine subclass,): uitree_editor.Engine for the Editor's kinds on the engine in use

    def __init__(self, ctx, names=None):
        self._surf, self._theme, self._damage = ctx.surface, ctx.theme, ctx.damage
        build = self.BUILD[0]
        eng = self.ENGINE[0] if self.ENGINE else _engine_class(False)
        if eng.__module__.rpartition(".")[2] == "uitree_editor":     # the Editor's kinds (runtime.uitree_editor on the host)
            eng = _engine_class(False, True)
        self.engine = eng(S.decl_of(S.module_of(build)), build, ctx.ui, ctx.surface, ctx.damage,
                          getattr(ctx, "files", None), env=ctx)

    def is_app(self, cart):
        return bool(self.IS_APP and self.IS_APP[0](cart))

    def open(self):
        self.engine.open()
        self._damage.all()

    def relayout(self, w, h, fs, cs=None):
        self.engine.root = None

    def close(self):
        self.engine.close()

    def draw(self, dt):
        cv = self._surf.canvas()
        th = self._theme.colors()
        desk = metric_class() == DESK
        win = self._surf.windowed()
        fs = max(1, getattr(cv, "font_scale", 1))
        cs = max(fs, self._surf.chrome_scale() or fs)
        content = content_of(cv.w, cv.h, 0 if win else bar_rows(cs))
        self.engine.hosted(dt, cv, th, content, desk)
        self.engine._ui.begin(cv, th, content, desk, win)
        self.engine.paint(dt, cv, th, content, desk)

    def handle_input(self, i):
        return self.engine.keys(i)

    def handle_pointer(self, px, py, click):
        return self.engine.point(px, py, click)

    def has_page(self):
        return bool(self.engine.has_page())

    def back(self):
        return self.engine.back()

    @property
    def idle_owed(self):
        return self.engine.idle_owed

    def idle_step(self):
        self.engine.idle_step()


def env():
    """What the host handed the engine running this handler: an app's
    AppContext, a bridged page's console or surface. A module's handlers
    reach the console's services through it, so two consoles in one process
    each reach their own."""
    a = _NOW.app
    if not isinstance(a, Engine) or _NOW.building:
        raise S.UIError("A10", "uitree_app.env() outside a handler",
                        fix="ask it in a handler or a hook; a build names state, it reads no service")
    return a.env


def app_layer(id, title, build, is_app=None, needs=(), engine=None):
    """The AppLayer class of an app whose screen is `build` (the module's
    root page): what its manifest's `entry` names. Its title is the
    module's `ui.app(...)` declaration's (§19.1), else `title`. `engine=` is
    the replay when the build uses the Editor's kinds
    (`uitree_editor.Engine`)."""
    if not callable(build):
        raise S.UIError("N01", "app_layer(%r): build is not a function" % id,
                        fix="pass the module's page function itself: app_layer(%r, %r, build)" % (id, title))
    if engine is not None and not (isinstance(engine, type) and issubclass(engine, Engine)):
        raise S.UIError("N01", "app_layer(%r): engine=%r is not an Engine subclass" % (id, engine),
                        fix="engine=uitree_editor.Engine, or leave it out")
    ad = S.decl_of(S.module_of(build)).app
    if ad is None and title is None:
        raise S.UIError("D04", "app %r has no ui.app declaration" % id, where=(S.module_of(build), 0),
                        fix='ui.app("Title", icon="...") at the top of %s' % S.module_of(build))
    if ad is not None:
        if ad.back != "pages" and ad.back not in S.decl_of(S.module_of(build)).actions:
            raise S.UIError("A01", "ui.app back=%r names no action" % ad.back, where=ad.where,
                            fix='declare ui.action(%r, ...), or back="pages"' % ad.back)
        title = ad.title()
    return type(title.replace(" ", "") + "App", (App,), {
        "id": id, "TITLE": title, "BUILD": (build,), "IS_APP": (is_app,) if is_app else None,
        "ENGINE": (engine,) if engine is not None else None,
        "NEEDS": App.NEEDS + tuple(needs)})


class Page(Engine):
    """A `uitree` page hosted by a surface still drawn with today's verbs
    (the bridge): the surface begins the frame on its own host and routes
    the keys to it; `paint` draws the page into its content rect."""

    def __init__(self, build, host, damage, files=None, close=None, gen=False, env=None):
        Engine.__init__(self, S.decl_of(S.module_of(build)), build, host, None, damage, files, gen, env)
        self.close_root = close


# -- which engine ------------------------------------------------------------------

ENGINES = ("reference", "c")
# The file whose word ("reference" or "c") picks a board's engine at boot, for diagnosis.
ENGINE_FILE = "/uitree_engine"


def _default():
    """The C engine on the host and on every board whose image carries
    native/moy_uitree; the reference on a board without it, or where
    `ENGINE_FILE` names it."""
    if sys.implementation.name != "micropython":
        return "c"
    try:
        with open(ENGINE_FILE) as f:
            name = f.read().strip()
        if name in ENGINES:
            return name
    except OSError:
        pass
    try:
        import moy_uitree  # noqa: F401
    except ImportError:
        return "reference"
    return "c"


DEFAULT = _default()
_IN_USE = [DEFAULT]


def use_engine(name):
    """The engine the apps and hosted pages made from now on run on:
    "reference" (`uitree_ref`'s replay) or "c" (native/moy_uitree, through
    uitree_c). It
    starts on `DEFAULT`; an app made with `engine=` keeps its own."""
    if name not in ENGINES:
        raise S.UIError("N01", "uitree_app.use_engine(%r)" % (name,), expected=", ".join(ENGINES),
                        fix='use_engine("reference") or use_engine("c")')
    _IN_USE[0] = name


def engine_in_use():
    return _IN_USE[0]


def _engine_class(page, editor=False):
    """The class a page (or an app's engine) is made of on the engine in
    use; `editor` for the Editor's kinds."""
    if _IN_USE[0] == "c":
        import uitree_c
        if editor:
            return uitree_c.CEditorPage if page else uitree_c.CEditorEngine
        return uitree_c.CPage if page else uitree_c.CEngine
    if editor:
        import uitree_ref_editor
        return uitree_ref_editor.Page if page else uitree_ref_editor.Engine
    import uitree_ref
    return uitree_ref.Page if page else uitree_ref.Engine


def hosted_page(build, host, damage, files=None, close=None, gen=False, env=None):
    """A `uitree` page hosted by a surface still drawn with today's verbs
    (`Page`), on the engine in use."""
    return _engine_class(True)(build, host, damage, files=files, close=close, gen=gen, env=env)


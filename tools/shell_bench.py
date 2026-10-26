#!/usr/bin/env python3
# Map (grep -n a name to jump there):
#   Run                the shell's scenarios over p4_bench's Bench, PERF under diag
#   Run.apps           an app's open, idle, content drag and window drag
#   Run.editor         the Editor's tabs
#   Run.editor_more    the Code scroll, the pickers, the desk's workspaces, the docked playtest
#   Run.paint_more     Paint's window drag, the collector's stops under a long stroke
#   Run.ui_page        the public UI API's fixture page: idle and a held arrow
#   Run.dialogs        the Editor's dialogs: Cart info, Blocks' number and name prompts
#   Run.pause          the pause menu over a running cart, and Resume; the stop card
#   gate               one board's whole run, in order, into a directory
#   report             a run's tables, beside an earlier run's, with flags
"""The shell's scenarios on a board, and one board's measurement run.

    python3 tools/shell_bench.py --board tdeck --out DIR [--only GROUPS]
    python3 tools/shell_bench.py --board p4 --out DIR --gate
    python3 tools/shell_bench.py --board p4 --out DIR --report [--against DIR]

Without `--gate` it runs the shell groups on the board as it is and writes
`DIR/BOARD_shell.json` (`--name` for another file name). Each row is
`tools/p4_bench.py`'s end-to-end painted-frame meter around one gesture (its
`Bench`: a sample is a `ws.frame()` that painted, so an idle row reads 0
frames, the pass), and, for a row that names a steady state, the same gesture
repeated for about 5.5 s under PERF DIAG with the PERF samples' medians beside
it, the collector's stops summed (`gc`). The groups, in the order they run:

    mem       heapcaps and the python heap on a settled desk
    idle      the home (or desk) idle
    menu      the system menu's open, idle and a move along its rows
    apps      Calc, Settings, Files, Appearance, Storybook, Get Carts
    paint     Paint's open, idle, stroke and (windowed) window drag
    projects  the PROJECTS window's drag (windowed tiers)
    notes     Notes' editor typed into, one key every 4 frames
    editor    the Editor on Star Catcher: each tab, the window, then memory
    more      the Code scroll, the sprite pickers' open, the desk Editor's
              workspaces and its docked playtest
    myart     My Art as `apps` takes an app
    gc        the collector's stops under 27 s of Paint strokes
    uipage    tests/fixtures/ui_page's carts (pushed by --gate): idle, a held arrow
    dialogs   the Editor's Cart info dialog and Blocks' number and name
              prompts: open, typed into (a key every 0.25 s), Cart info's
              fields moved
    pause     the pause menu over Star Catcher: open, idle, a move, Resume;
              the stop card over its run stopped: open, a move
    memhome   memory after the home's carousel ran (--gate runs it after --home)

The baseline groups (mem to editor) keep the order they were first measured
in, since a window drag reads what the earlier groups left on the desk; the
rest run after them.

`--gate` is one board's run as the 2026-10 look's baseline and phase gate took
it: a reboot and 20 s settle (so "after boot" memory is a fresh desk), the
fixture pages pushed, the shell groups, `p4_bench.py --home`, the memory after
it, the default panel run, `--painter`, `--repaint` of the apps and the
Editor's Code tab, `--popovers`, `--desk-scale` on a windowed tier, then
`tools/p4_perf.py`'s roster with the diag off and on, and the desk put back.
Each step's output and log land in DIR under the board's name. WiFi stays
as the board has it; `BOARD_shell.json`'s first row records it.

`--report` renders DIR's files for the board as markdown tables; with
`--against` each row sits beside the same row of the earlier run, with the
median's change and a flag where it grew by more than 10% and 2 ms, crossed a
60 Hz frame, an idle row started painting or the loop rate fell by more than
10%. Numbers live in issues (#66, #58, #202); this tool makes them.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

PERF_FIELDS = ("busy", "draw", "flush", "logic", "render", "chrome",
               "wmr", "wmw", "wms", "fence_ms", "gfence_ms")

GROUPS = ("mem", "idle", "menu", "apps", "paint", "projects", "notes",
          "editor", "more", "myart", "gc", "uipage", "dialogs", "pause")

UI_PAGES = ("tests/fixtures/ui_page/page_py.moy",
            "tests/fixtures/ui_page/page_lua.moy")

REPAINT = "settings,files,calc,appearance,storybook,getcarts,artwork,myart,editor:code"


def med(vals):
    v = [x for x in vals if isinstance(x, (int, float))]
    return round(statistics.median(v), 1) if v else None


TYPER = """
ws._tn = 0
ws._tc = 0
ws._ts = "the quick brown fox jumps over the lazy dog 0123456789 "
if not hasattr(ws, '_type_installed'):
    _of2 = ws.frame
    def _f2(dt, _of2=_of2):
        if ws._tn > 0:
            ws._tc += 1
            if ws._tc % 4 == 0:
                fe = ws.player._focus_editor
                if fe is not None:
                    fe.key(ord(ws._ts[ws._tn % len(ws._ts)]))
                    ws._tn -= 1
                else:
                    ws._tn = 0
        return _of2(dt)
    ws.frame = _f2
    ws._type_installed = True
"""

# A key press handed to one handler, as the router would: `pressed(name)`
# answers for one name and `last_key` types nothing.
PRESS = """
class _SBK:
    last_key = 0
    def __init__(self, n):
        self.n = n
    def pressed(self, n):
        return n == self.n
ws._SBK = _SBK
"""


def parse_heapcaps(line):
    """A HEAPCAPS line -> {heap: (total, free, largest, low)} in bytes, the gc
    heap as (held, live, areas)."""
    out = {}
    for tok in (line or "").split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            try:
                out[k] = tuple(int(x) for x in v.split("/"))
            except ValueError:
                pass
    return out


def parse_mem(line):
    m = re.search(r"live=(\d+)k free=(\d+)k", line or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


class Run:
    def __init__(self, b, out, rows=None):
        import p4_bench as pb
        self.pb = pb
        self.b = b
        self.out = out
        self.bench = pb.Bench(b)
        self.rows = rows if rows is not None else []
        self.windowed = bool(pb.pyval(b, "ws.wm.has_desk"))
        self.ex(PRESS)

    def ex(self, code, timeout=60):
        """Statements on the console; a failure is printed with its error."""
        try:
            return self.b.pyexec(code, timeout=timeout, strict=True)
        except Exception as e:  # noqa: BLE001 -- the run goes on, the log says why
            print("      ! %s: %s" % (code.strip().splitlines()[0][:60], e))
            return False

    def no_cart(self):
        """No cart left running: the next group, and the next tool, start
        from the desk."""
        for _ in range(3):
            if not self.b.state().get("cart"):
                return
            self.b.leave_cart()
            self.b.drain(1.0)
        print("      ! a cart is still running: %s" % self.b.state().get("cart"))

    def save(self):
        with open(self.out, "w") as f:
            json.dump(self.rows, f, indent=1)

    def home(self):
        if self.windowed:
            self.pb.home(self.b)
        else:
            self.ex("ws.go_home()")
            self.b.drain(1.5)

    def _row(self, name, r, **extra):
        row = {"name": name, "stats": r["stats"],
               "layers": dict(sorted(r["layers"].items(),
                                     key=lambda kv: -kv[1])[:3])}
        row.update(extra)
        self.rows.append(row)
        self.save()
        return row

    def frames(self, name, action, settle=0.0):
        """Painted-frame stats from Bench around one `action`, no PERF."""
        self.bench.start()
        action()
        if settle:
            self.b.drain(settle)
        return self._row(name, self.bench.stop(name))

    def perf(self, action, secs=5.5):
        """`action` repeated for about `secs` under PERF DIAG: the samples'
        medians, the collector's stops summed and the longest."""
        b = self.b
        from runtime.perf_line import parse_perf
        b.cmd("diag 1")
        n0 = len(b.lines)
        t0 = time.time()
        while time.time() - t0 < secs:
            action()
        b.drain(0.3)
        b.cmd("diag 0")
        samples = [p for p in (parse_perf(l) for l in b.lines[n0:]) if p]
        if not samples:
            return None
        p = {"samples": len(samples)}
        fp = [s["fps"] for s in samples if s.get("fps")]
        p["drawn_fps"] = med([f[0] for f in fp])
        p["loop_fps"] = med([f[1] for f in fp])
        for k in PERF_FIELDS:
            p[k] = med([s.get(k) for s in samples])
        gc = [s["gc"] for s in samples if isinstance(s.get("gc"), tuple)]
        if gc:
            p["gc_n"] = int(sum(g[0] or 0 for g in gc))
            p["gc_us"] = int(sum(g[1] or 0 for g in gc))
            p["gc_max_us"] = int(max(g[2] or 0 for g in gc))
        p["secs"] = round(time.time() - t0, 1)
        return p

    def measure(self, name, action, perf=True, perf_secs=5.5):
        self.bench.start()
        action()
        row = self._row(name, self.bench.stop(name))
        if perf:
            row["perf"] = self.perf(action, perf_secs)
            self.save()
        return row

    # -- gestures ---------------------------------------------------------
    def idle(self, secs=3.0):
        return lambda: self.b.drain(secs)

    def swipe(self, x0, y0, x1, y1, frames=25):
        def f():
            self.b.swipe(x0, y0, x1, y1, frames=frames)
            self.b.drain(1.0)
        return f

    def win_drag(self):
        def f():
            self.b.cmd("drag 60 6")
            self.b.wait_line("drag done", 30)
            self.b.drain(0.8)
        return f

    def presses(self, target, names, gap=0.3):
        """Key presses handed to `target`'s handle_input, `gap` s apart, the
        frame marked as the router marks it: not where the keys are a
        running cart's or a popover's that paints its own change."""
        def f():
            for n in names:
                self.b.cmd("py %s.handle_input(ws._SBK(%r)) or ws.wm.keys_to_cart() or "
                           "ws.wm.popover_takes_keys() or ws.mark_dirty() or 1"
                           % (target, n), wait_for="PY")
                self.b.drain(gap)
        return f

    def content(self):
        """(x, y, w, h) of what the top app shows, in swipe coordinates."""
        b = self.b
        if self.windowed:
            st = b.state()
            keys = [k for k in st.get("order", []) if k != "desktop"]
            if keys:
                w = st["wins"][keys[-1]]
                return (w[0] + 1, w[1] + 1 + w[4], w[2], w[3] - w[4])
        W, H = self.pb.pyval(b, "(ws.sys_canvas.w, ws.sys_canvas.h)")
        return (0, H // 8, W, H * 3 // 4)

    def vert(self):
        x, y, w, h = self.content()
        return self.swipe(x + w // 2, y + h * 3 // 4, x + w // 2, y + h // 4)

    def horiz(self):
        x, y, w, h = self.content()
        return self.swipe(x + w * 3 // 4, y + h // 2, x + w // 4, y + h // 2,
                          frames=20)

    def open_app(self, app):
        if app == "settings":
            self.ex("ws.open_settings()")
            return True
        return bool(self.pb.pyval(self.b,
                                  "ws.open_app(ws._apps_by_id[%r])" % app))

    # -- the groups -------------------------------------------------------
    def meta(self):
        st = self.b.state()
        self.rows.append({"name": "meta", "wifi": st.get("wifi"),
                          "wifi_held": st.get("wifi_held"),
                          "windowed": self.windowed,
                          "theme": self.pb.pyval(self.b, "ws.look.theme_name")})
        self.save()

    def mem(self, label):
        b = self.b
        hc = b.cmd("heapcaps", wait_for="HEAPCAPS")
        m = b.cmd("mem", wait_for="REMOTE mem", timeout=20)
        self.rows.append({"name": "memory: %s" % label, "heapcaps": hc,
                          "mem": m})
        self.save()
        print("  MEM", label, hc, m)

    def apps(self, kinds):
        b = self.b
        for app, label, drag in kinds:
            self.home()
            self.bench.start()
            ok = self.open_app(app)
            b.drain(2.5)
            row = self._row("app open: %s" % label,
                            self.bench.stop("app open: %s" % label))
            if not ok:
                row["note"] = "app not available"
                self.save()
                continue
            b.drain(2.0)
            self.measure("app idle: %s" % label, self.idle(3.0))
            if drag == "v":
                self.measure("app drag: %s" % label, self.vert())
            elif drag == "h":
                self.measure("app drag: %s" % label, self.horiz())
            if self.windowed:
                self.measure("window drag: %s" % label, self.win_drag())
        self.home()

    def sysmenu(self):
        b = self.b
        self.home()
        b.drain(1.0)
        self.frames("system menu open",
                    lambda: self.ex("ws.toggle_sysmenu()"), 1.5)
        self.measure("system menu idle", self.idle(3.0))
        self.ex("ws.toggle_sysmenu()")
        b.drain(1.0)

    def sysmenu_move(self):
        b = self.b
        self.home()
        b.drain(1.0)
        self.ex("ws.toggle_sysmenu()")
        b.drain(1.5)

        def move():
            # as the router hands the menu a key: the frame is marked only
            # where the popover does not paint its own change
            for d in ("down",) * 3 + ("up",) * 3:
                b.cmd("py ws._sysmenu_layer.handle_input(ws._SBK(%r)) or "
                      "ws.wm.popover_takes_keys() or ws.mark_dirty() or 1" % d,
                      wait_for="PY")
                b.drain(0.3)
        self.measure("system menu move (6 rows)", move)
        self.ex("ws.sysmenu.open and ws.toggle_sysmenu()")
        b.drain(1.0)

    def paint(self):
        b = self.b
        self.home()
        self.frames("app open: Paint", lambda: self.open_app("artwork"), 2.5)
        b.drain(1.5)
        self.measure("Paint idle", self.idle(3.0))
        x, y, w, h = self.content()
        self.measure("Paint stroke",
                     self.swipe(x + w // 4, y + h // 2, x + w * 3 // 4,
                                y + h // 2 + h // 6, frames=40))
        self.home()

    def paint_more(self):
        """Paint's window drag (windowed), then the collector under 27 s of
        strokes drawn back and forth: its stops, their longest, how often."""
        b = self.b
        self.home()
        self.open_app("artwork")
        b.drain(3.0)
        if self.windowed:
            self.measure("window drag: Paint", self.win_drag())
        x, y, w, h = self.content()
        k = [0]

        def strokes():
            k[0] += 1
            dy = (k[0] % 5) * h // 8
            x0, x1 = (x + w // 6, x + w * 5 // 6)
            if k[0] % 2:
                x0, x1 = x1, x0
            b.swipe(x0, y + h // 4 + dy // 2, x1, y + h // 3 + dy // 2,
                    frames=120)
        self.bench.start()
        p = self.perf(strokes, 27.0)
        row = self._row("Paint strokes, 27 s (the collector's stops)",
                        self.bench.stop("Paint strokes, 27 s"))
        row["perf"] = p
        if p and p.get("gc_n"):
            row["gc_every_s"] = round(p["secs"] / p["gc_n"], 1)
        self.save()
        self.home()

    def notes(self):
        b = self.b
        pb = self.pb
        self.home()
        line = b.cmd("run Notes", wait_for="REMOTE run", timeout=20)
        print("   run Notes:", line)
        b.drain(3.0)
        got = False

        def cart_xy(cx, cy):
            if self.windowed:
                x, y, w, h = self.content()
            else:
                W, H = pb.pyval(b, "(ws.sys_canvas.w, ws.sys_canvas.h)")
                x, y, w, h = 0, 0, W, H
            k = max(1, min(w // 320, h // 240))
            return (x + (w - 320 * k) // 2 + cx * k,
                    y + (0 if self.windowed else (h - 240 * k) // 2) + cy * k)
        for steps in (((60, 63),), ((45, 30), (88, 123))):
            for cx, cy in steps:
                sx, sy = cart_xy(cx, cy)
                b.tap(sx, sy, settle=1.5)
            if pb.pyval(b, "ws.player._focus_editor is not None"):
                got = True
                break
        self.rows.append({"name": "notes: editor focused", "ok": got})
        if got:
            self.ex(TYPER)

            def typing():
                self.ex("ws._tn = 40")
                b.drain(3.5)
            self.measure("Notes typing (1 key / 4 frames)", typing)
            self.measure("Notes idle (editor open)", self.idle(3.0))
        self.save()
        b.leave_cart()
        self.no_cart()
        self.home()

    def projects_window(self):
        self.home()
        self.ex("ws.open_projects()")
        self.b.drain(12.0)
        self.measure("window drag: PROJECTS", self.win_drag())
        self.home()

    def _open_editor(self, title):
        self.home()
        self.ex("ws._ec = [c for c in ws.carts.all "
                               "if c['title'] == %r][0]" % title)
        self.bench.start()
        self.ex("ws.open_in_editor(ws._ec)")
        self.b.drain(3.0)
        return self.bench.stop("editor open (%s)" % title)

    def editor(self, title):
        b = self.b
        pb = self.pb
        self._row("editor open", self._open_editor(title))
        st = b.state()
        print("   editor state:", st.get("stack"), st.get("screen"))
        for tab, label in (("cards", "Config"), ("blocks", "Blocks"),
                           ("code", "Code"), ("paint", "Sprites"),
                           ("map", "Map"), ("scene", "Scene"),
                           ("music", "Music")):
            self.bench.start()
            self.ex("ws.editor_app.set_tab(%r)\nws.mark_dirty()" % tab)
            b.drain(2.5)
            self._row("editor tab switch: %s" % label,
                      self.bench.stop("editor tab switch: %s" % label),
                      tab_now=pb.pyval(b, "ws.editor_app.tab"))
            self.measure("editor tab idle: %s" % label, self.idle(3.0))
        if self.windowed:
            self.measure("window drag: Editor", self.win_drag())
        self.mem("editor open")

    def editor_more(self, title):
        """The Editor past its tabs: a Code scroll, the sprite pickers' open
        over the sheet, and on a desk Editor the five workspaces and the
        playtest Play docks beside them."""
        b = self.b
        pb = self.pb
        self._open_editor(title)
        self.ex("ws.editor_app.set_tab('code')\nws.mark_dirty()")
        b.drain(2.5)
        self.measure("editor Code scroll", self.vert())
        self.ex("ws.editor_app.set_tab('paint')\nws.mark_dirty()")
        b.drain(2.5)
        self.frames("editor sprite pickers open",
                    lambda: self.ex("ws.paint_layer._open_pickers()"), 1.5)
        self.measure("editor sprite pickers idle", self.idle(3.0))
        self.ex("ws.paint_layer._pop = False\nws.mark_dirty()")
        b.drain(1.0)
        if not pb.pyval(b, "ws.editor_app._desk() is not None"):
            self.home()
            return
        keys = pb.pyval(b, "list(__import__('editor_panes').KEYS)") or []
        for key in keys + keys[:1]:
            self.frames("editor workspace: %s" % key,
                        lambda: self.ex("ws.editor_app._desk().switch(%r)\n"
                                             "ws.mark_dirty()" % key), 2.5)
            self.measure("editor workspace idle: %s" % key, self.idle(3.0))
        self.frames("editor Play (the playtest docks)",
                    lambda: self.ex("ws.defer(ws._leave_menu)"), 5.0)
        self.measure("editor playtest running beside the Editor", self.idle(3.0))
        self.ex("ws.wm.close_player()")
        b.drain(2.0)
        self.home()

    def myart(self):
        self.apps([("myart", "My Art", "v")])

    def ui_page(self):
        """The fixture page each runtime draws: settled, it paints nothing;
        an arrow held 3 s moves the focus down the page."""
        b = self.b
        for title in ("UI Page Py", "UI Page Lua"):
            self.home()
            line = b.cmd("run %s" % title, wait_for="REMOTE run", timeout=20)
            print("   run %s:" % title, line)
            b.drain(3.0)
            if not b.state().get("cart"):
                self.rows.append({"name": "ui page: %s" % title,
                                  "note": "did not run: %s" % line})
                self.save()
                continue
            self.measure("ui page idle: %s" % title, self.idle(3.0))

            def held():
                self.ex("ws.input.source('local').set_button('down', True)")
                b.drain(3.0)
                self.ex("ws.input.source('local').set_button('down', False)")
                b.drain(0.5)
            self.measure("ui page held arrow: %s" % title, held)
            b.leave_cart()
            self.no_cart()
        self.home()

    def pooled(self, name, setup, action, n=4, settle=1.0):
        """`n` repeats of `action` after an unmeasured `setup`, their
        painted frames pooled into one row."""
        ft = []
        layers = {}
        for _ in range(n):
            setup()
            self.b.drain(settle)
            self.bench.start()
            action()
            self.b.drain(1.0)
            r = self.bench.stop(name)
            ft += self.pb.pyval(self.b, "list(ws._ft)") or []
            layers = r["layers"]
        return self._row(name, {"stats": self.pb.stats(ft), "layers": layers})

    def typed(self, name, key_expr, text, gap=0.25):
        """`text` typed a byte every `gap` s through `key_expr % byte`."""
        def f():
            for ch in text:
                self.ex((key_expr % ord(ch)) + "\nws.mark_dirty()")
                self.b.drain(gap)
        return self.frames(name, f, 0.5)

    def dialogs(self, title):
        """The Editor's dialogs, each over its page: Blocks' number and name
        prompts and Cart info."""
        b = self.b
        self._open_editor(title)
        self.ex("ws.editor_app.set_tab('blocks')\nws.mark_dirty()")
        b.drain(2.5)
        shut = "ws.block_ui.blk_kbd and ws.block_ui._blk_kbd_cancel()\nws.mark_dirty()"
        for kind, opener, text in (
                ("number", "ws.block_ui._blk_open_kbd('num', cur=5, block={'t': 'set', "
                           "'p': {}}, slot='value', allow_block=True)", "1234567890"),
                ("name", "ws.block_ui._blk_open_kbd('var', var='score', slot_target=None)",
                 "abcdefghij")):
            self.pooled("Blocks %s dialog open" % kind, lambda: self.ex(shut),
                        lambda opener=opener: self.ex(opener + "\nws.mark_dirty()"))
            self.typed("Blocks %s dialog typing" % kind, "ws.block_ui._blk_kbd_key(%d)", text)
            self.ex(shut)
            b.drain(1.0)
        self.ex("ws.editor_app.set_tab('cards')\nws.mark_dirty()")
        b.drain(2.5)
        shut = "ws.cards_layer.prompt and ws.cards_layer._close_prompt()"
        self.pooled("Cart info dialog open", lambda: self.ex(shut),
                    lambda: self.ex("ws.cards_layer._open_meta()"))
        self.typed("Cart info dialog typing", "ws.cards_layer._prompt_key(%d)", "Hello world")
        def fields():
            for n in ("down", "up") * 6:
                self.b.cmd("py ws.cards_layer._prompt_input(ws._SBK(%r)) and "
                           "ws.mark_dirty() or 1" % n, wait_for="PY")
                self.b.drain(0.3)
        self.frames("Cart info dialog moving fields", fields, 0.5)
        self.ex(shut)
        self.home()

    def pause(self, title):
        b = self.b
        pb = self.pb
        self.home()
        line = b.cmd("run %s" % title, wait_for="REMOTE run", timeout=20)
        print("   run %s:" % title, line)
        b.drain(4.0)
        self.frames("pause menu open (over %s)" % title,
                    lambda: self.ex("ws.pause.show()"), 1.5)
        self.measure("pause menu idle", self.idle(3.0))
        self.measure("pause menu move (6 rows)",
                     self.presses("ws.pause", ("down",) * 3 + ("up",) * 3))
        self.bench.start()
        self.ex("ws.pause.close()")
        b.drain(1.5)
        row = self._row("Resume: the cart's frames after it",
                        self.bench.stop("Resume"))
        row["first_ms"] = pb.pyval(b, "ws._ft[:3]")
        self.save()
        # the stop card: the run stopped as a crash stops it, over its last frame
        self.ex("ws.cart_error = 'TypeError: a bench stop'\nws.mark_dirty()")
        b.drain(1.5)
        if pb.pyval(b, "ws.pause.open and ws.pause.stopped"):
            self.pooled("stop card open (over %s stopped)" % title,
                        lambda: self.ex("ws.pause.dismiss()\nws.mark_dirty()"),
                        lambda: self.ex("ws.pause.stop()"), n=5)
            self.frames("stop card move (its rows)",
                        self.presses("ws.pause", ("down", "down", "up", "up") * 3), 0.5)
        else:
            print("      ! the stop card did not open")
        # in one exec: a frame between the two would stop the run again
        self.ex("ws.cart_error = None\nws.pause.dismiss()")
        b.leave_cart()
        self.no_cart()
        self.home()


def run_groups(b, out, only):
    r = Run(b, out)
    want = (lambda n: n in only) if only else (lambda n: n in GROUPS)
    print("windowed:", r.windowed)
    if not only:
        r.meta()
    if want("mem"):
        r.home()
        b.drain(5)
        r.mem("after boot (desk)")
    if want("idle"):
        r.home()
        r.measure("home/desk idle", r.idle(3.0))
    if want("menu"):
        r.sysmenu()
    if want("apps"):
        r.apps([("calc", "Calc", "h"), ("settings", "Settings", "v"),
                ("files", "Files", "v"), ("appearance", "Appearance", "v"),
                ("storybook", "Storybook", "v"),
                ("getcarts", "Get Carts", "v")])
    if want("paint"):
        r.paint()
    if want("projects") and r.windowed:
        r.projects_window()
    if want("notes"):
        r.notes()
    if want("editor"):
        r.editor("Star Catcher")
    if want("more"):
        r.editor_more("Star Catcher")
    if want("myart"):
        r.myart()
    if want("menu"):
        r.sysmenu_move()
    if want("paint"):
        r.paint_more()
    if want("uipage"):
        r.ui_page()
    if want("dialogs"):
        r.dialogs("Star Catcher")
    if want("pause"):
        r.pause("Star Catcher")
    if want("memhome"):
        r.mem("after the home's carousel")
    r.no_cart()
    r.home()


# -- --gate: one board's run ---------------------------------------------------

def gate(board, out, port):
    py = sys.executable
    tool = lambda name: os.path.join(HERE, name)  # noqa: E731
    pa = ["--port", port] if port != "auto" else []
    windowed = None

    def step(name, argv, log):
        if argv[2:3] in ([tool("p4_bench.py")], [tool("p4_perf.py")]):
            subprocess.call(bp + ["desk"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, cwd=ROOT)
        print("== %s: %s" % (board, name), flush=True)
        t0 = time.time()
        with open(os.path.join(out, log), "w") as f:
            rc = subprocess.call(argv, stdout=f, stderr=subprocess.STDOUT,
                                 cwd=ROOT)
        print("   rc=%d in %.0f s" % (rc, time.time() - t0), flush=True)
        return rc

    o = lambda suffix: os.path.join(out, "%s_%s" % (board, suffix))  # noqa: E731
    bp = [py, tool("board.py"), board] + pa
    step("reboot", bp + ["reboot"], "%s_reboot.log" % board)
    step("wait", bp + ["wait"], "%s_wait.log" % board)
    time.sleep(20)
    for cart in UI_PAGES:
        step("push %s" % os.path.basename(cart), bp + ["push", cart],
             "%s_push.log" % board)
    me = [py, "-u", os.path.abspath(__file__), "--board", board] + pa
    step("shell", me + ["--out", out], "%s_shell.log" % board)
    bench = [py, "-u", tool("p4_bench.py"), "--board", board] + pa
    step("home", bench + ["--home", "--out", o("home.md")], "%s_home.log" % board)
    step("memory after home", me + ["--out", out, "--only", "memhome",
                                    "--name", "%s_memhome.json" % board],
         "%s_memhome.log" % board)
    step("default run", bench + ["--out", o("all.md")], "%s_all.log" % board)
    step("painter", bench + ["--painter", "--out", o("painter.md")],
         "%s_painter.log" % board)
    step("repaint", bench + ["--repaint", REPAINT, "--out", o("repaint.md")],
         "%s_repaint.log" % board)
    step("popovers", bench + ["--popovers", "--out", o("popovers.md")],
         "%s_popovers.log" % board)
    try:
        with open(o("shell.json")) as f:
            windowed = next((r.get("windowed") for r in json.load(f)
                             if r.get("name") == "meta"), None)
    except (OSError, ValueError):
        pass
    if windowed:
        step("desk scale", bench + ["--desk-scale", "--out", o("deskscale.md")],
             "%s_deskscale.log" % board)
    perf = [py, "-u", tool("p4_perf.py"), "--board", board] + pa
    step("roster", perf, "%s_roster.log" % board)
    step("roster --diag", perf + ["--diag"], "%s_roster_diag.log" % board)
    step("desk", bp + ["desk"], "%s_desk.log" % board)


# -- --report: the tables ------------------------------------------------------

def _md_rows(path, prefix):
    rows = []
    try:
        lines = open(path).read().splitlines()
    except OSError:
        return rows
    for ln in lines:
        c = [x.strip() for x in ln.strip().strip("|").split("|")]
        if len(c) < 7 or c[0] in ("scenario", "") or c[0].startswith("---"):
            continue
        st = None
        if c[1] not in ("-", "0"):
            st = {"n": int(c[1]), "median": int(c[2]), "p90": int(c[3]),
                  "max": int(c[4])}
        rows.append({"name": prefix + c[0], "stats": st, "layers_txt": c[6]})
    return rows


def _json_rows(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


MD_PARTS = (("home.md", "home: "), ("all.md", "default run: "),
            ("repaint.md", "repaint run: "),
            ("popovers.md", "popovers: "), ("deskscale.md", "desk scale: "))


def load_run(d, board):
    rows = _json_rows(os.path.join(d, "%s_shell.json" % board))
    late = _json_rows(os.path.join(d, "%s_notes.json" % board))
    if late:
        names = {r["name"] for r in late}
        rows = [r for r in rows if r["name"] not in names] + late
    rows += _json_rows(os.path.join(d, "%s_memhome.json" % board))
    for suffix, prefix in MD_PARTS:
        rows += _md_rows(os.path.join(d, "%s_%s" % (board, suffix)), prefix)
    return rows


def load_roster(d, board, suffix):
    out = {}
    try:
        lines = open(os.path.join(d, "%s_%s" % (board, suffix))).read().splitlines()
    except OSError:
        return out
    for ln in lines:
        m = re.match(r"^(\S.*?)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+)\s*(\S*)", ln)
        if m:
            out[m.group(1)] = (float(m.group(2)), float(m.group(3)),
                               m.group(5) or "")
    return out


def _vsyncs(ms):
    return math.ceil(ms / (1000.0 / 60) - 1e-9)


def _cell(a, b, fmt="%s"):
    fa = "-" if a is None else fmt % a
    fb = "-" if b is None else fmt % b
    return fb if fa == fb else "%s → %s" % (fa, fb)


def _layers(r):
    if "layers_txt" in r:
        return r["layers_txt"]
    return ", ".join("%s %dms" % kv for kv in (r.get("layers") or {}).items())


def _perf_txt(pa, pb_):
    if not pa and not pb_:
        return ""
    pa = pa or {}
    pb_ = pb_ or {}
    bits = []
    for k, lab in (("loop_fps", "loop"), ("busy", "busy"), ("draw", "draw"),
                   ("wmw", "wmw"), ("fence_ms", "fence")):
        a, b = pa.get(k), pb_.get(k)
        if a is None and b is None:
            continue
        bits.append("%s %s" % (lab, _cell(a, b, "%g")))
    if pb_.get("gc_n") is not None:
        bits.append("gc %d/%dms max %dms" % (pb_["gc_n"], pb_["gc_us"] // 1000,
                                             pb_["gc_max_us"] // 1000))
    return ", ".join(bits)


def flags(name, sa, sb, pa, pb_):
    out = []
    ma = sa["median"] if sa else None
    mb = sb["median"] if sb else None
    if "idle" in name and "until idle" not in name and not sa and sb and sb["n"] > 1:
        out.append("paints at idle (%d frames)" % sb["n"])
    if ma is not None and mb is not None:
        if mb > ma * 1.10 and mb - ma >= 2:
            out.append("+%d%%" % round(100.0 * (mb - ma) / max(ma, 1)))
        if _vsyncs(mb) > _vsyncs(ma) and mb - ma >= 1:
            out.append("%d→%d frames at 60 Hz" % (_vsyncs(ma), _vsyncs(mb)))
    la = (pa or {}).get("loop_fps")
    lb = (pb_ or {}).get("loop_fps")
    if la and lb and lb < la * 0.9:
        out.append("loop -%d%%" % round(100.0 * (la - lb) / la))
    return "; ".join(out)


def _mem_txt(ra, rb):
    ha = parse_heapcaps((ra or {}).get("heapcaps"))
    hb = parse_heapcaps((rb or {}).get("heapcaps"))
    bits = []
    for k in ("psram", "sram", "dma"):
        a, b = ha.get(k), hb.get(k)
        if not b:
            continue
        av = None if not a else a[1] // 1024
        d = "" if av is None else " (%+dK)" % (b[1] // 1024 - av)
        bits.append("%s free %s%s, largest %dK, low-water %dK"
                    % (k, _cell(av, b[1] // 1024, "%dK"), d, b[2] // 1024,
                       b[3] // 1024))
    a, b = ha.get("gc"), hb.get("gc")
    if b:
        bits.append("gc heap holds %s, live %s, %s areas"
                    % (_cell(a and a[0] // 1024, b[0] // 1024, "%dK"),
                       _cell(a and a[1] // 1024, b[1] // 1024, "%dK"),
                       _cell(a and a[2], b[2], "%d")))
    a, b = parse_mem((ra or {}).get("mem")), parse_mem((rb or {}).get("mem"))
    if b:
        bits.append("python heap after collect: live %s free %s"
                    % (_cell(a and a[0], b[0], "%dK"), _cell(a and a[1], b[1], "%dK")))
    return "; ".join(bits)


def report(d, board, against=None):
    after = load_run(d, board)
    before = {r["name"]: r for r in load_run(against, board)} if against else {}
    seen = set()
    out = ["| scenario | frames | median ms | Δ median | p90 ms | max ms | "
           "top layers (median ms) | PERF diag | flag |",
           "|---|---|---|---|---|---|---|---|---|"]
    mems = []
    for r in after:
        n = r.get("name", "")
        if n == "meta" or "ok" in r:
            continue
        a = before.get(n)
        seen.add(n)
        if n.startswith("memory: "):
            mems.append("- %s: %s" % (n[8:], _mem_txt(a, r)))
            continue
        sa = (a or {}).get("stats")
        sb = r.get("stats")
        dm = ""
        if sa and sb:
            dm = "%+d" % (sb["median"] - sa["median"])
        elif sb and a is not None and not sa:
            dm = "was 0 frames"
        elif a is None and against:
            dm = "new"
        note = r.get("note") or ""
        if r.get("first_ms"):
            note = "first frames %s ms" % r["first_ms"]
        if r.get("gc_every_s"):
            note = "a collection every %s s" % r["gc_every_s"]
        g = lambda s, k: s[k] if s else None  # noqa: E731
        if a is None:
            sa, pa, c = sb, r.get("perf"), (lambda x, y, f: "-" if y is None
                                            else f % y)
        else:
            pa, c = a.get("perf"), _cell
        out.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            n, c(g(sa, "n") or 0, g(sb, "n") or 0, "%s"),
            c(g(sa, "median"), g(sb, "median"), "%s"), dm,
            c(g(sa, "p90"), g(sb, "p90"), "%s"),
            c(g(sa, "max"), g(sb, "max"), "%s"), _layers(r) + (
                (" " + note) if note else ""),
            _perf_txt(pa, r.get("perf")),
            flags(n, sa, sb, pa, r.get("perf")) if a is not None else ""))
    gone = [n for n in before if n not in seen and n != "meta"
            and "ok" not in before[n]]
    text = "\n".join(out)
    if gone:
        text += "\n\nIn the earlier run only: " + "; ".join(gone)
    if mems:
        text += "\n\n**Memory**\n\n" + "\n".join(mems)
    try:
        with open(os.path.join(d, "%s_painter.md" % board)) as f:
            text += "\n\n**--painter** (us a call)\n\n" + f.read().strip()
    except OSError:
        pass
    ra = load_roster(d, board, "roster.log")
    rd = load_roster(d, board, "roster_diag.log")
    ba = load_roster(against, board, "roster.log") if against else {}
    bd = load_roster(against, board, "roster_diag.log") if against else {}
    if ra:
        rows = ["| cart | fps (diag off) | worst window | fps (diag on) | "
                "phase ms (diag on) |", "|---|---|---|---|---|"]
        for cart, (fps, worst, _x) in ra.items():
            b0 = ba.get(cart, (None, None, ""))
            d1 = rd.get(cart, (None, None, ""))
            d0 = bd.get(cart, (None, None, ""))
            rows.append("| %s | %s | %s | %s | %s |" % (
                cart, _cell(b0[0], fps, "%.1f"), _cell(b0[1], worst, "%.1f"),
                _cell(d0[0], d1[0], "%.1f"), _cell(d0[2] or None, d1[2] or None)))
        text += "\n\n**Carts**\n\n" + "\n".join(rows)
    return text


def main():
    from p4_autotest import add_board_args, board_from_args
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_board_args(ap)
    ap.add_argument("--out", required=True, help="the directory a run writes to")
    ap.add_argument("--only", default="",
                    help="comma-separated groups (default: %s)" % ",".join(GROUPS))
    ap.add_argument("--name", default=None,
                    help="the rows' file in --out (default BOARD_shell.json)")
    ap.add_argument("--gate", action="store_true",
                    help="the board's whole run: reboot, groups, p4_bench, roster")
    ap.add_argument("--report", action="store_true",
                    help="render --out's tables for the board")
    ap.add_argument("--against", default=None,
                    help="with --report: an earlier run's directory")
    args = ap.parse_args()
    if args.report:
        print(report(args.out, args.board, args.against))
        return 0
    os.makedirs(args.out, exist_ok=True)
    if args.gate:
        gate(args.board, args.out, args.port)
        return 0
    b = board_from_args(args)
    try:
        run_groups(b, os.path.join(args.out, args.name or
                                   "%s_shell.json" % args.board),
                   set(filter(None, args.only.split(","))))
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

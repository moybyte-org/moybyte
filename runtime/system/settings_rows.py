"""Settings' rows page as a `uitree` build, which `SettingsLayer` hosts on
its rows' host (`RowsPage`): the captioned groups of rows, a row for each
setting this board has, then About.

A row a board lacks (the Bluetooth keyboard, the idle ladder's rungs, the
web console, the update flows, the C6, the USB drive, the last crash, a
gated toggle, the theme package's Layout) is a condition over a bool the
`rows_poll` hook keeps from `SettingsLayer._settings_rows()`, which stays
the one place a capability decides a row. The hook runs before each frame
and reads every value the rows show, as the page's poll did; a row's
answer calls into the layer (`uitree_app.env()`): its pages, its level and
action verbs, the console's setters.

The Name is a commit field: typed into a draft, stored by Enter (focus then
goes to the row above), by focus leaving it and by every way out of
Settings (`RowsPage.leave`). Where nothing can type it shows read-only,
with its note under the group.
"""

import uitree as ui
import uitree_app
import ssh_service as _ssh
import wallclock as _wc
from idle_ladder import ROWS as IDLE_ROWS, STEPS as IDLE_STEPS, DIM
from settings_layer import (SETTINGS_TOGGLES, LEVELS, NAME_MAX, NAME_NO_KEYBOARD, say, _capital,
                            _idle_word, _SIZES, _HOME_WORDS, _STABLE_BETA)

TOGGLES = tuple((t[0], say(t[1]), t[3]) for t in SETTINGS_TOGGLES)    # (state, label, verb)
GATED = tuple(t[0] for t in SETTINGS_TOGGLES if t[4] is not None)
PLAY = ("two_player", "steady", "crisp_pixels", "show_fps")
HOMES = ("theme", "carousel", "grid", "icons")
SCALES = tuple(str(f) for f in sorted(_SIZES))
CHANNELS = ("stable", "beta")
IDLE = tuple((key, say(text)) for key, _rung, text, _d in IDLE_ROWS)
STEP_WORDS = tuple(str(t) for t in IDLE_STEPS)

# the rows a board adds, by the row key `_settings_rows()` names them with
CAPS = ("bluetooth", "layout_theme", "crash", "usb_drive", "webhost", "update", "ota_channel",
        "update_c6", "idle_dim", "idle_blank")

s = ui.state(
    wifi_word="", bt_word="", theme_title="", tz_place="", crash_word="", web_word="",
    paired_words="", ach_words="",
    font_scale=ui.enum(*SCALES, init=SCALES[0]),
    layouts=ui.model(key="v", v="", word=""),
    layout_theme=ui.row_of("layouts"),
    home_style=ui.enum(*HOMES, init="theme"),
    volume=0, brightness=0,
    ota_channel=ui.enum(*CHANNELS, init="stable"),
    name="", name_shown="", name_hint="", can_type=True,
    about=ui.model(key="label", label="", value=""),
    battery=0, has_battery=False,
    has_updates=False,
    **dict([("has_" + k, False) for k in CAPS + GATED]
           + [(t[0], False) for t in TOGGLES]
           + [(k, ui.enum(*STEP_WORDS, init=STEP_WORDS[0])) for k, _l in IDLE]))


ui.labels("sizes", dict((str(f), w) for f, w in _SIZES.items()))
ui.labels("homes", dict(zip(HOMES, _HOME_WORDS)))
ui.labels("channels", dict(zip(CHANNELS, _STABLE_BETA)))
ui.labels("idle", dict((str(t), _idle_word(t)) for t in IDLE_STEPS))


# -- what the rows show, read before each frame ---------------------------------

def _model(name, rows):
    """A model written when its rows move (an equal model write is a new
    version: the page's gen would move every frame)."""
    last = uitree_app.env().rows.last
    if last.get(name) != rows:
        last[name] = rows
        setattr(s, name, [dict(r) for r in rows])


def _words(sl, kept):
    ws = sl.ws
    w = sl._wifi.station()
    ssid = None
    if w is not None:
        try:
            connected, ssid, _ip = w.status()
            ssid = ssid if connected else None
        except Exception:  # noqa: BLE001
            ssid = None
    if kept.get("ssid", ()) != ssid:
        kept["ssid"] = ssid
        kept["wifi_word"] = str(ssid)[:16] if ssid else "Off"
    s.wifi_word = kept["wifi_word"]
    bt = sl._bt_service()
    if bt is not None:
        try:
            enabled, state, name, _preferred, _error = bt.settings_status()
        except Exception:  # noqa: BLE001 -- a radio hiccup reads Off
            enabled, state, name = False, "error", None
        if kept.get("bt_on", ()) != enabled or kept.get("bt_state") != state or kept.get("bt_name", ()) != name:
            kept["bt_on"], kept["bt_state"], kept["bt_name"] = enabled, state, name
            kept["bt_word"] = (str(name)[:12] if name else state[:1].upper() + state[1:]) if enabled else "Off"
        s.bt_word = kept["bt_word"]
    look = ws.look
    if kept.get("theme") is not look.theme_name or kept.get("variant") is not look.theme_variant:
        kept["theme"], kept["variant"] = look.theme_name, look.theme_variant
        kept["theme_title"] = sl._theme_title()
    s.theme_title = kept["theme_title"]
    zone = _wc.zone()
    if kept.get("zone", ()) is not zone:
        kept["zone"] = zone
        kept["tz_place"] = _wc.place(zone)
    s.tz_place = kept["tz_place"]
    if s.has_crash:
        s.crash_word = sl.crash_word()
    if s.has_webhost:
        s.web_word = ws.web.label() if ws.web.serving() else "Off"
    s.paired_words = sl._paired_words


_HAS = tuple(("has_" + k, k) for k in CAPS + GATED)     # (the state, the row key it asks for)
_SCALE_WORD = dict((f, str(f)) for f in _SIZES)
_STEP_WORD = dict((t, str(t)) for t in IDLE_STEPS)


def rows_poll():
    """Every value the rows show, read before each frame. A board pays for
    each string, tuple and list a frame makes, so what is read from a source
    that has not moved (the rows the layer offers, the settings rows' gen,
    the look, the wifi's answer) is the value kept from the last read."""
    sl = uitree_app.env()
    ws = sl.ws
    kept = sl.rows.kept
    rows = sl._settings_rows()                  # memoized by the layer: the same tuple until a row comes or goes
    if kept.get("rows") is not rows:
        kept["rows"] = rows
        kept["keys"] = tuple(r[0] for r in rows)
    keys = kept["keys"]
    for name, k in _HAS:
        setattr(s, name, k in keys)
    s.has_updates = s.has_webhost or s.has_update or s.has_ota_channel or s.has_update_c6
    _words(sl, kept)
    for name, _label, _verb in TOGGLES:
        setattr(s, name, bool(getattr(ws, name, False)))
    look = ws.look
    s.font_scale = _SCALE_WORD.get(look.font_scale, SCALES[0])
    if s.has_layout_theme:
        pick = look.layout_theme()
        if kept.get("lgen", -1) != look.layout_gen or kept.get("lpick", ()) != pick:
            kept["lgen"], kept["lpick"] = look.layout_gen, pick
            vals = (None,) + tuple(look.layout_choices())
            if pick is not None and pick not in vals:
                vals = vals + (pick,)
            _model("layouts", tuple((("v", "" if c is None else c),
                                     ("word", "Theme" if c is None else _capital(str(c)))) for c in vals))
        s.layout_theme = "" if pick is None else pick
    pick = look.home_style()
    s.home_style = "theme" if pick is None else pick
    gen = ws.system.gen()
    if kept.get("gen", -1) != gen or kept.get("system") is not ws.system:
        kept["gen"], kept["system"] = gen, ws.system      # the settings rows moved: read again
        kept["volume"] = sl.level("volume")[0]
        kept["brightness"] = sl.level("brightness")[0]
        kept["name"] = str(ws.system.get("name", "") or "")
        kept["name_shown"] = _ssh.name(ws)
    s.volume = kept["volume"]
    s.brightness = kept["brightness"]
    if s.has_idle_blank:
        lad = ws.idle_ladder
        for key, _label in IDLE:
            setattr(s, key, _STEP_WORD.get(lad.value(key), STEP_WORDS[0]))
    if s.has_ota_channel:
        s.ota_channel = "beta" if ws.toggles.ota_channel() == "unstable" else "stable"
    s.can_type = bool(ws.text.can_type())
    s.name = kept["name"]
    s.name_shown = kept["name_shown"]
    lvl = ws.battery_level()
    s.has_battery = lvl is not None
    s.battery = lvl or 0
    a, t = ws.ach.count(), ws.ach.total()
    if kept.get("ach", -1) != a or kept.get("ach_total", -1) != t:
        kept["ach"], kept["ach_total"] = a, t
        kept["ach_words"] = "%d of %d" % (a, t)
    s.ach_words = kept["ach_words"]
    if kept.get("about") is not sl._about:
        kept["about"] = sl._about
        _model("about", tuple((("label", k), ("value", v)) for k, v in sl._about))


def rows_opened():
    if not s.name_hint:
        s.name_hint = _ssh.default_name()
    rows_poll()


# -- a row's answer --------------------------------------------------------------

def _page(name):
    def open_():
        uitree_app.env().open_page(name)
    return open_


def _act(key):
    def act():
        uitree_app.env()._activate_settings_action(key)
    return act


def web_toggle():
    uitree_app.env().ws.web.toggle()


def ach_open():
    uitree_app.env().ws.ach_ui._secret_taps = 0
    uitree_app.env().open_page("achievements")


def font_set():
    look = uitree_app.env().ws.look
    scales = look.FONT_SCALES
    cur = scales.index(look.font_scale) if look.font_scale in scales else 0
    look.cycle_font_scale(scales.index(int(s.font_scale)) - cur)


def layout_set():
    look = uitree_app.env().ws.look
    keys = [r.v for r in s.layouts]
    pick = look.layout_theme()
    cur = keys.index("" if pick is None else pick)
    look.cycle_layout_theme(keys.index(s.layout_theme or "") - cur)


def home_set():
    look = uitree_app.env().ws.look
    pick = look.home_style()
    look.cycle_home_style(HOMES.index(s.home_style) - (0 if pick is None else HOMES.index(pick)))


def channel_set():
    ws = uitree_app.env().ws
    cur = 1 if ws.toggles.ota_channel() == "unstable" else 0
    ws.toggles.cycle_channel(CHANNELS.index(s.ota_channel) - cur)


def volume_set():
    uitree_app.env().set_level("volume", s.volume)


def brightness_set():
    uitree_app.env().set_level("brightness", s.brightness)


def name_set():
    """The Name, stored once an edit (Enter, focus leaving, Settings left)."""
    ws = uitree_app.env().ws
    if s.name != str(ws.system.get("name", "") or ""):
        ws.system.set("name", s.name)
        _ssh.rename(ws)


def name_done():
    ui.focus("tz")


def _toggled(name, verb):
    def set_():
        getattr(uitree_app.env().ws.toggles, verb)(getattr(s, name))
    return set_


def _idle_set(key):
    def set_():
        uitree_app.env().ws.idle_ladder.set(key, int(getattr(s, key)))
    return set_


ui.hook("rows_opened", rows_opened)
ui.hook("rows_poll", rows_poll)
for _key, _label, _does in (
        ("wifi", "WIFI", _page("wifi")), ("bluetooth", "BLUETOOTH KEYBOARD", _page("bt")),
        ("appearance", "APPEARANCE", _act("appearance")), ("tz", "TIME ZONE", _page("tz")),
        ("usb_drive", "USB DRIVE", _act("usb_drive")), ("webhost", "WEB CONSOLE", web_toggle),
        ("paired", "PAIRED BROWSERS", _page("paired")), ("update", "UPDATE FW", _act("update")),
        ("update_online", "UPDATE ONLINE", _act("update_online")),
        ("update_c6", "UPGRADE C6 RADIO", _act("update_c6")), ("crash", "LAST CRASH", _page("crash")),
        ("permissions", "App permissions", _page("permissions")),
        ("achievements", "Achievements", ach_open), ("licences", "Licences", _page("licences")),
        ("log", "System log", _page("log"))):
    ui.action(_key, say(_label) if _label.isupper() else _label, _does, hint="Open")
ui.hook("font_set", font_set)
ui.hook("layout_set", layout_set)
ui.hook("home_set", home_set)
ui.hook("channel_set", channel_set)
ui.hook("volume_set", volume_set)
ui.hook("brightness_set", brightness_set)
ui.hook("name_set", name_set)
ui.hook("name_done", name_done)
for _name, _label, _verb in TOGGLES:
    ui.hook(_name + "_set", _toggled(_name, _verb))
for _key, _label in IDLE:
    ui.hook(_key + "_set", _idle_set(_key))


# -- the page -------------------------------------------------------------------

def _toggle_row(name, label):
    ui.toggle(label, name, on=name + "_set", hint="{%s?Turn off:Turn on}" % name,
              shown=("has_" + name) if name in GATED else None)


def rows_page():
    with ui.page(""):
        with ui.group("General"):
            ui.link("wifi", "{wifi_word}")
            ui.link("bluetooth", "{bt_word}", shown="has_bluetooth")
            ui.link("appearance", "{theme_title}")
            ui.seg("Text size", "font_scale", labels="sizes", look="cycle", on="font_set")
            ui.seg("Layout", "layout_theme", text="{.word}", look="cycle", on="layout_set",
                   shown="has_layout_theme")
            ui.seg("Home style", "home_style", labels="homes", look="cycle", on="home_set")
            ui.slider("Volume", "volume", LEVELS["volume"][0], LEVELS["volume"][1], look="steps",
                      icon="{volume?:mute}", on="volume_set")
            ui.slider("Brightness", "brightness", LEVELS["brightness"][0], LEVELS["brightness"][1],
                      look="steps", on="brightness_set")
            ui.link("tz", "{tz_place}")
            ui.field("Name", "name", NAME_MAX, commit=True, hint="{name_hint}", on="name_done",
                     changed="name_set", shown="can_type")
            ui.info("Name", "{name_shown}", shown="!can_type")
            ui.link("usb_drive", shown="has_usb_drive")
        with ui.group(NAME_NO_KEYBOARD, shown="!can_type"):
            pass
        with ui.group("Play"):
            for name, label, _verb in TOGGLES:
                if name in PLAY:
                    _toggle_row(name, label)
        with ui.group("Power", shown="has_idle_blank"):
            for key, label in IDLE:
                ui.seg(label, key, labels="idle", look="cycle", on=key + "_set",
                       shown="has_idle_dim" if key == IDLE[0][0] and IDLE_ROWS[0][1] == DIM else None)
        with ui.group("Updates", shown="has_updates"):
            ui.link("webhost", "{web_word}", shown="has_webhost")
            ui.link("paired", "{paired_words}", shown="has_webhost")
            ui.link("update", shown="has_update")
            ui.seg("Channel", "ota_channel", labels="channels", on="channel_set", shown="has_ota_channel")
            ui.link("update_online", shown="has_ota_channel")
            ui.link("update_c6", shown="has_update_c6")
        with ui.group("Developer"):
            for name, label, _verb in TOGGLES:
                if name not in PLAY:
                    _toggle_row(name, label)
            ui.link("crash", "{crash_word}", shown="has_crash")
        with ui.group("About"):
            with ui.rows("about"):
                ui.info("{.label}", "{.value}")
            ui.info("Battery", "{battery}%", shown="has_battery")
            ui.link("permissions")
            ui.link("achievements", "{ach_words}")
            ui.link("licences")
            ui.link("log")


class RowsPage:
    """The rows page on Settings' rows host: opened at each visit, polled and
    drawn each frame, its draft Name written on every way out."""

    def __init__(self, layer, host):
        self.engine = uitree_app.hosted_page(rows_page, host, self, gen=True, env=layer)
        self._ws = layer.ws
        self.last = {}                  # each model's rows as this page last wrote them
        self.kept = {}                  # what rows_poll read from a source that has not moved since

    def all(self):
        """The page's damage: Settings draws another frame."""
        self._ws.request_frame()

    def open(self):
        self.engine.open(hook="rows_opened")

    def draw(self, cv, th, rect, desk, dt):
        self.engine.run(rows_poll, action="rows_poll")
        ui.commit(self.engine)
        self.engine.paint(dt, cv, th, rect, desk)

    def keys(self, i):
        self.engine.keys(i, damage=False)

    def focus(self, key):
        self.engine.want_focus = key

    def focused(self):
        return self.engine.focus_key

    def leave(self):
        self.engine.leave()

    def draft(self, key):
        """A commit field's text typed and not yet written, or None."""
        return self.engine.draft(key)

    def kinds(self):
        """Each stop the last frame drew, by node key: link, toggle, cycle,
        seg, slider or field."""
        out = {}
        for key, kind in self.engine.stop_kinds().items():
            if kind in ("link", "toggle", "cycle", "seg", "slider", "field"):
                out[key] = kind
        return out

    def drawn(self, key):
        """The kind of node `key` the last frame drew, or None."""
        kind = self.engine.stop_kinds().get(key)
        return "seg" if kind == "cycle" else kind

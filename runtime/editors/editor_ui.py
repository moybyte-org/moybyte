"""The Editor's head row as a `uitree` page, lent to the shell's bar
(`tools lend="bar"`, replayed into the bar's zone by
`uitree_editor.BarZone`), one lender shown at a time. On a handheld: the
cart's tabs, undo, redo and Play, Restart and Stop in Play's place while a
playtest runs; in theme mode, Done, the theme's two tabs, its name, undo
and redo. On the desk: the cart's workspaces in their place, Restart
marked while an edit waits for it; in theme mode, the theme's two tabs,
undo and redo (the desk's Done is its window's).

`EditorApp` (editor_app.py) is the host: it writes the console's values
this row shows into the state (`head_sync`) and does what each action
asks (`_activate_zone_tab`, `theme_tab`, `close_theme`).

On the desk each workspace is a page of its own (`SPACES`): its panes
(`ui.panes`), each pane's body a `ui.legacy` node the desk draws
(`EditorDesk.pane_body`). Its shape and starting sizes follow the area and
the cart through state its host writes (`EditorDesk._sync`): `big` (the
1024 arrangement, a Run slot over a context pane; else one tabbed pane at
the right), the start sizes in desk units (`col_w`, `run_h`, `art_w`,
`set_w`, `theme_w`), the cart's tabs (`has_*`), `by_blocks` and `game`;
the held sizes (`z_*`) are the dividers' and the board remembers them."""
import uitree as ui
from editor_panes import WORKSPACES, KEYS, THEME, CONTENTS

# The host (an EditorApp), set by it once.
_host = [None]

TABS = ("cards", "blocks", "code", "paint", "map", "scene", "music")
THEME_TABS = ("theme_config", "theme_sprites")

# The desk's tabbed panes' tabs, an enum each: the contents they show.
TAB_SETS = {"t_side": ("sprites", "sounds", "maps", "log"),
            "t_side_n": ("game", "sprites", "sounds", "maps", "log"),
            "t_run": ("scene", "game"),
            "t_blocks_n": ("scene", "game", "palette", "made"),
            "t_map": ("map", "game"),
            "t_actors": ("actor", "stamps"),
            "t_actors_n": ("actor", "stamps", "game"),
            "t_song_n": ("game", "song")}

s = ui.state(
    tab=ui.enum(*TABS, init="cards"),
    theme_tab=ui.enum(*THEME_TABS, init="theme_config"),
    theme=False, theme_name="",
    has_blocks=True, has_code=True, has_paint=True, has_map=True, has_scene=True,
    has_music=True,
    can_undo=False, can_redo=False, playing=False,
    desk=False, space=ui.enum(*KEYS, init="settings"),
    has_space_settings=True, has_space_code=True, has_space_art=True, has_space_world=True,
    has_space_sound=True, restart_owed=False,
    # the desk's workspaces (`SPACES`): what the area and the cart make them
    big=True, by_blocks=False, game=True, has_cards=True, grown=ui.opt(""),
    col_w=340, run_h=282, art_w=400, set_w=360, theme_w=352,
    z_cards=ui.opt(0), z_right=ui.opt(0), z_run=ui.opt(0), z_palette=ui.opt(0),
    z_paint=ui.opt(0), z_sheet=ui.opt(0), z_sounds=ui.opt(0), z_theme=ui.opt(0),
    **dict((k, ui.enum(*v, init=v[0])) for k, v in TAB_SETS.items())
)
ui.labels("TABS", {"cards": "Config", "blocks": "Blocks", "code": "Code", "paint": "Sprites",
                   "map": "Map", "scene": "Scene", "music": "Music"})
ui.labels("TAB_ICONS", {"cards": "edit", "blocks": "blocks", "code": "code", "paint": "paint",
                        "map": "map", "scene": "scene", "music": "music"})
ui.labels("THEME_TABS", {"theme_config": "Config", "theme_sprites": "Sprites"})
ui.labels("THEME_ICONS", {"theme_config": "sliders", "theme_sprites": "sprite"})
ui.labels("SPACES", {k: label for k, label, _i in WORKSPACES})
ui.labels("PANES", {c: v[0] for c, v in CONTENTS.items()})
ui.labels("SPACE_ICONS", {k: icon for k, _l, icon in WORKSPACES})


def open_tab():
    """A tab taken on the bar (the open one again too: the Code tab's
    scripts)."""
    _host[0].open_zone_tab(s.tab)


def open_space():
    """A desk workspace taken on the bar."""
    _host[0].open_zone_tab("ws:" + s.space)


def undo():
    _host[0].open_zone_tab("undo")


def redo():
    _host[0].open_zone_tab("redo")


def play():
    _host[0].open_zone_tab("play")


def restart():
    _host[0].open_zone_tab("restart")


def stop():
    _host[0].open_zone_tab("stop")


def theme_open_tab():
    _host[0].theme_tab(s.theme_tab)


def theme_done():
    h = _host[0]
    h.ws.defer(h.close_theme)


ui.action("open_tab", "Open", open_tab)
ui.action("open_space", "Open", open_space)
ui.action("undo", "Undo", undo, when="can_undo", off="grey")
ui.action("redo", "Redo", redo, when="can_redo", off="grey")
ui.action("play", "Play", play)
ui.action("restart", "Restart", restart)
ui.action("stop", "Stop", stop)
ui.action("theme_open_tab", "Open", theme_open_tab)
ui.action("theme_done", "Done", theme_done)


def head():
    with ui.page("Editor"):
        with ui.tools("head", lend="bar", shown="!theme & !desk"):
            ui.tabs("tab", "TABS", icons="TAB_ICONS", on="open_tab",
                    offered={"blocks": "has_blocks", "code": "has_code", "paint": "has_paint",
                             "map": "has_map", "scene": "has_scene", "music": "has_music"})
            ui.button("undo", icon="undo", alone=True)
            ui.button("redo", icon="redo", alone=True)
            ui.button("play", icon="run", kind="primary", shown="!playing")
            ui.button("restart", icon="rotate", kind="primary", shown="playing")
            ui.button("stop", icon="stop", kind="primary", shown="playing")
        with ui.tools("theme_head", lend="bar", shown="theme & !desk"):
            ui.button("theme_done", icon="back", alone=True)
            ui.tabs("theme_tab", "THEME_TABS", icons="THEME_ICONS", on="theme_open_tab")
            ui.label("{theme_name}")
            ui.button("undo", icon="undo", alone=True)
            ui.button("redo", icon="redo", alone=True)
        with ui.tools("desk_head", lend="bar", shown="desk & !theme"):
            ui.tabs("space", "SPACES", icons="SPACE_ICONS", on="open_space",
                    offered={k: "has_space_" + k for k in KEYS})
            ui.button("undo", icon="undo", alone=True)
            ui.button("redo", icon="redo", alone=True)
            ui.button("play", icon="run", kind="primary", shown="!playing")
            ui.button("restart", icon="rotate", kind="primary", shown="playing",
                      marked="restart_owed")
            ui.button("stop", icon="stop", kind="primary", shown="playing")
        with ui.tools("desk_theme", lend="bar", shown="desk & theme"):
            ui.tabs("theme_tab", "THEME_TABS", icons="THEME_ICONS", on="theme_open_tab")
            ui.button("undo", icon="undo", alone=True)
            ui.button("redo", icon="redo", alone=True)


# -- the desk's workspaces --------------------------------------------------

# The desk (an EditorDesk), set by it once: it draws each pane's body.
_desk = [None]

# The condition each content shows under: the cart offers its Editor tab
# (or the tab it serves), the Game while no window of its own holds it.
_WHEN = {"cards": "has_cards", "code": "has_code", "blocks": "has_blocks",
         "paint": "has_paint", "sheet": "has_paint", "sprites": "has_paint",
         "map": "has_map", "maps": "has_map", "scene": "has_scene", "actor": "has_scene",
         "stamps": "has_scene", "music": "has_music", "sounds": "has_music",
         "song": "has_music", "game": "game"}


def _body(key):
    def draw(cv, th, x, y, w, h, focused, dt):
        _desk[0].pane_body(key, dt)
    return draw


def _one(c, also=None, **p):
    """A pane of one content, keyed by it, shown where its content is and
    `also` holds."""
    label, icon, _tab, least = CONTENTS[c]
    when = " & ".join(w for w in (_WHEN.get(c), also) if w) or None
    with ui.pane(c, title=label, icon=icon, min=least, shown=when, **p):
        ui.legacy("body", draw=_body(c))


def _tabbed(key, tabs, **p):
    """A pane of several contents, its tabs `tabs`' values."""
    vals = TAB_SETS[tabs]
    off = dict((c, _WHEN[c]) for c in vals if c in _WHEN)
    with ui.pane(key, tabs=tabs, labels="PANES", offered=off or None,
                 min=dict((c, CONTENTS[c][3]) for c in vals), **p):
        ui.legacy("body", draw=_body(key))


def settings_space():
    with ui.page("Editor"):
        with ui.panes("row", "root", grown="grown"):
            _one("cards", size="z_cards", start="set_w")
            _one("game", grow=True, head=False)


def code_space():
    with ui.page("Editor"):
        with ui.panes("row", "root", grown="grown"):
            # a blocks cart's: the palette, the blocks, the run and the made code
            _one("palette", size="z_palette", start=200, also="by_blocks & big")
            _one("blocks", grow=True, also="by_blocks")
            with ui.panes("col", "bright", size="z_right", start="col_w", shown="by_blocks & big"):
                _tabbed("run", "t_run", size="z_run", start="run_h")
                _one("made", grow=True)
            _tabbed("bside_n", "t_blocks_n", size="z_right", start="col_w", shown="by_blocks & !big")
            # ...the code's
            _one("code", grow=True, also="!by_blocks")
            with ui.panes("col", "right", size="z_right", start="col_w", shown="!by_blocks & big"):
                _one("game", size="z_run", start="run_h")
                _tabbed("side", "t_side", grow=True)
            _tabbed("side_n", "t_side_n", size="z_right", start="col_w", shown="!by_blocks & !big")


def art_space():
    with ui.page("Editor"):
        with ui.panes("row", "root", grown="grown"):
            _one("paint", size="z_paint", start="art_w")
            with ui.panes("col", "right", grow=True, shown="big"):
                _tabbed("mapgame", "t_map", grow=True)
                _one("sheet", size="z_sheet", start=162)
            _tabbed("mapgame_n", "t_map", grow=True, shown="!big")


def world_space():
    with ui.page("Editor"):
        with ui.panes("row", "root", grown="grown"):
            _one("scene", grow=True)
            with ui.panes("col", "right", size="z_right", start="col_w", shown="big"):
                _one("game", size="z_run", start="run_h")
                _tabbed("actors", "t_actors", grow=True)
            _tabbed("actors_n", "t_actors_n", size="z_right", start="col_w", shown="!big")


def sound_space():
    with ui.page("Editor"):
        with ui.panes("row", "root", grown="grown"):
            _one("sounds", size="z_sounds", start=200, also="big")
            _one("music", grow=True)
            with ui.panes("col", "right", size="z_right", start="col_w", shown="big"):
                _one("game", size="z_run", start="run_h")
                _one("song", grow=True)
            _tabbed("song_n", "t_song_n", size="z_right", start="col_w", shown="!big")


def theme_space():
    with ui.page("Editor"):
        with ui.panes("row", "root", grown="grown"):
            _one("theme_config", size="z_theme", start="theme_w")
            _one("theme_sprites", grow=True)


SIZES = ("z_cards", "z_right", "z_run", "z_palette", "z_paint", "z_sheet", "z_sounds", "z_theme")
SPACES = {"settings": settings_space, "code": code_space, "art": art_space,
          "world": world_space, "sound": sound_space, THEME: theme_space}

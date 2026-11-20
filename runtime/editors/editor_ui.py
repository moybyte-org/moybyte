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
asks (`_activate_zone_tab`, `theme_tab`, `close_theme`)."""
import uitree as ui
from editor_panes import WORKSPACES, KEYS

# The host (an EditorApp), set by it once.
_host = [None]

TABS = ("cards", "blocks", "code", "paint", "map", "scene", "music")
THEME_TABS = ("theme_config", "theme_sprites")

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
)
ui.labels("TABS", {"cards": "Config", "blocks": "Blocks", "code": "Code", "paint": "Sprites",
                   "map": "Map", "scene": "Scene", "music": "Music"})
ui.labels("TAB_ICONS", {"cards": "edit", "blocks": "blocks", "code": "code", "paint": "paint",
                        "map": "map", "scene": "scene", "music": "music"})
ui.labels("THEME_TABS", {"theme_config": "Config", "theme_sprites": "Sprites"})
ui.labels("THEME_ICONS", {"theme_config": "sliders", "theme_sprites": "sprite"})
ui.labels("SPACES", {k: label for k, label, _i in WORKSPACES})
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

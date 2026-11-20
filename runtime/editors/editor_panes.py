"""The desk Editor's workspaces (docs/media/look_2026-10/EDITOR_LAYOUT.md,
concept B, and MAXIMISE_VARIANTS.md's Resize, R-01): what a pane can show
and the sizes the panes keep. The workspaces' trees are pages
(runtime/editors/editor_ui.py's `SPACES`), laid out by the Editor engine's
panes (runtime/uitree/uitree_editor.py).

Each pane but one in a split starts at a fixed size (the pictures' pane
sizes at 1024 x 600 and 800 x 480) and the one that grows takes what is
left, so a bigger window gives the scrolling pane (Code, the Reference,
Art's Map) the difference and the canvas and the game keep their zoom. A
divider moves to any pixel and stops at its neighbour's minimum; the
minimums are the T-Deck's (docs/history/studio_2026-09.md §4.2): the game
at 1x under its header, a canvas the T-Deck's page under its bar plus the
header. A pane that scrolls has the floor that keeps its header.

A pane is a tuple of content keys (`CONTENTS`): one is a pane with a title,
several a tabbed pane. Sizes are in glass pixels at chrome size 1 and are
multiplied by `unit` (the desk's chrome size over the render scale)."""

GUTTER = 6                  # between panes and round the panes area
HEADER = 32                 # a pane's header row
EDGE = 3                    # the focused pane's `value` edge along its top
RADIUS = 8                  # a pane's card
HANDLE = (6, 36)            # a divider's handle, drawn
HANDLE_HIT = (16, 48)       # ...and the touch it takes
DRAG_SLOP = 8               # a touch drag's travel before it takes
KEY_STEP = 8                # Ctrl+Alt+arrow moves a divider this far

GAME_MIN = (320, 240 + HEADER)          # the game at 1x under its header
CANVAS_MIN = (320, 222 + HEADER)        # the T-Deck's page under its bar
FLOOR = (64, HEADER + 16)               # a scrolling pane keeps its header

# content -> (label, icon, Editor tab or None, minimum)
CONTENTS = {
    "cards": ("Settings", "sliders", "cards", FLOOR),
    "code": ("Code", "code", "code", FLOOR),
    "blocks": ("Blocks", "blocks", "blocks", FLOOR),
    "paint": ("Sprite", "sprite", "paint", CANVAS_MIN),
    "map": ("Map", "map", "map", CANVAS_MIN),
    "scene": ("Scene", "scene", "scene", CANVAS_MIN),
    "music": ("Sound", "music", "music", CANVAS_MIN),
    "game": ("Game", "game", None, GAME_MIN),
    "sheet": ("Sheet", "sprite", None, FLOOR),
    "sprites": ("Sprites", "sprite", None, FLOOR),
    "sounds": ("Sounds", "music", None, FLOOR),
    "maps": ("Map", "map", None, FLOOR),
    "log": ("Log", "doc", None, FLOOR),
    "song": ("Song", "music", None, FLOOR),
    "actor": ("Actor", "scene", None, FLOOR),
    "stamps": ("Stamps", "stamp", None, FLOOR),
    "palette": ("Block palette", "blocks", None, FLOOR),
    "made": ("Code", "lock", None, FLOOR),
    # the Editor's theme mode (runtime/editors/theme_editor.py): its two tabs
    "theme_config": ("Config", "sliders", "theme_config", FLOOR),
    "theme_sprites": ("Sprites", "sprite", "theme_sprites", (420, 330 + HEADER)),
}

# (key, label, icon) in the app's row's order; Ctrl+1 ... 5 is this order.
WORKSPACES = (
    ("settings", "Settings", "sliders"),
    ("code", "Code", "code"),
    ("art", "Art", "sprite"),
    ("world", "World", "scene"),
    ("sound", "Sound", "music"),
)
KEYS = tuple(k for k, _l, _i in WORKSPACES)
# The theme mode's one workspace: Config beside Sprites. Not on the app's row,
# which shows the two tabs instead.
THEME = "theme"

# The Editor tab each workspace stands for where one tab is asked of it
# (the handheld ladder's, the history's): its first pane's.
HOME_TAB = {"settings": "cards", "code": "code", "art": "paint",
            "world": "scene", "sound": "music"}

# The panes that serve one Editor tab's work beside it, by that tab: where
# the open cart has not got the tab (`EditorApp._offers`), they go with it.
SERVES = {"sheet": "paint", "sprites": "paint", "maps": "map", "sounds": "music",
          "song": "music", "actor": "scene", "stamps": "scene"}


def tab_for(content):
    """The Editor tab whose work pane `content` is: its own, else the one it
    serves (SERVES), else None (the Game, the Log)."""
    return CONTENTS[content][2] or SERVES.get(content)


def wide(w):
    """Whether a panes area `w` wide takes the 1024 arrangement (a Run slot
    over a context pane); narrower, one tabbed pane at the right."""
    return w >= 960

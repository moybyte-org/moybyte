# Moybyte block-programming model + blocks -> Python compiler (issue #29, Part 1).
#
# This is the data model and the compiler for the structured-outline block editor
# (a Scratch *look*, device-friendly mechanics: no dragging). A block program is
# the cart's SOURCE: it is stored as blocks.json beside main.py, and `compile_blocks`
# turns it into a normal `.moy` main.py (the icon -> block -> code ladder). The
# block editor runs ON THE DEVICE, so everything here is MicroPython-safe (no
# f-strings, no eval/exec/getattr/compile/open, json + plain string building only)
# and is frozen onto the device alongside console/editors/moy_carts.
#
# ============================================================================
# THE SCHEMA  (compact, json-serializable -- this is what blocks.json holds)
# ============================================================================
#
# A *block* is a small dict:
#
#     {"t": <type-id str>, "p": {<slot>: <value>, ...}, "c": [<child block>, ...]}
#
#   "t"  block type id, a key in CATALOG (e.g. "if", "spr", "set_var", "op_add").
#   "p"  param slots: slot-name -> value. A value is one of:
#          - a number or string LITERAL (for number / text / variable / dropdown
#            slots; a "variable" slot holds the variable NAME as a string, a
#            "dropdown" slot holds the chosen option string),
#          - a nested EXPRESSION block (a dict with the same shape) for an
#            "expr" slot -- operators, comparisons, var references, input readers.
#        "p" may be omitted/empty when a block takes no params.
#   "c"  child statement blocks (the nested body), for blocks whose catalog entry
#        has shape == "c-block" (events, control). Omitted/empty for plain
#        statements and for expression blocks.
#
# A *program* is:
#
#     {"vars": [<name str>, ...],
#      "lists": [<name str>, ...],
#      "scripts": [<event block>, ...]}
#
#   "vars"     declared variables (each becomes a module-level global initialized
#              to 0; `set_var`/`change_var` write them, `var` reads them).
#   "lists"    declared lists (#48; each becomes a module-level global initialized
#              to []; list_add/list_clear/list_remove_at write them, list_get/
#              list_len read them, and for_each iterates one). OPTIONAL -- an older
#              blocks.json with no "lists" key loads as having none (back-compat).
#   "scripts"  the top level: a flat list of EVENT blocks (on_start / on_update /
#              on_draw). Each event's "c" is its body. The compiler emits one cart
#              lifecycle function per event kind (_init/_update/_draw), so at most
#              one script of each kind is meaningful (extra ones of the same kind
#              are concatenated into the same function body, in order).
#
# Everything round-trips through json.dumps/json.loads unchanged.
#
# ============================================================================
# THE GENERATED CART  (what compile_blocks emits -- matches the real carts)
# ============================================================================
#
# A header comment, then `<var> = 0` module-level inits, then the lifecycle
# functions that exist in the program:
#
#     # Made with Moybyte blocks.
#     score = 0
#     x = 0
#
#     def _init():
#         global score, x
#         score = 0
#         ...
#
#     def _update(dt):
#         global x
#         if btn("left"):
#             x = x - 2
#
#     def _draw():
#         cls(col("black"))
#         spr(0, x, 100)
#
# The body calls ONLY the injected cart API verbs (cls/spr/print/rect/.../btn/
# btnp/touch/sfx/beep/col/rnd) -- the same names host_app.make_api injects -- so
# the output runs unchanged on host and device. Globals are hoisted with `global`
# wherever a function assigns one (MicroPython needs this), and never otherwise.

try:
    import json
except ImportError:  # pragma: no cover
    json = None


# ============================================================================
# Slot types (for the editor's param UI -- Part 2 reads these)
# ============================================================================

SLOT_NUMBER = "number"      # a numeric literal (int/float)
SLOT_TEXT = "text"          # a string literal (rendered quoted)
SLOT_VARIABLE = "variable"  # a variable NAME (one of program["vars"])
SLOT_LIST = "list"          # a list NAME (one of program["lists"]) -- #48
SLOT_DROPDOWN = "dropdown"  # one option from the slot's `options` list
SLOT_EXPR = "expr"          # a nested expression block (operator/var/input/...)


# ============================================================================
# Categories (Scratch-style grouping; drives the insert menu in Part 2)
# ============================================================================

CAT_EVENTS = "events"
CAT_CONTROL = "control"
CAT_DRAW = "draw"
CAT_INPUT = "input"
CAT_VARIABLES = "variables"
CAT_LISTS = "lists"          # #48: the multi-thing data type
CAT_ACTORS = "actors"        # #109: placed-actor scenes (for-each/touching/move/remove)
CAT_OPERATORS = "operators"
CAT_SOUND = "sound"
CAT_PROCS = "myblocks"       # #48: custom blocks (My Blocks / procedures)

CATEGORY_ORDER = [
    CAT_EVENTS, CAT_CONTROL, CAT_DRAW, CAT_INPUT,
    CAT_VARIABLES, CAT_LISTS, CAT_ACTORS, CAT_OPERATORS, CAT_SOUND, CAT_PROCS,
]

# Color name (MOY64) per category -- the Scratch *look* (Part 2 paints blocks
# with these; here so host and device agree on the palette).
CATEGORY_COLOR = {
    CAT_EVENTS: "brown",
    CAT_CONTROL: "orange",
    CAT_DRAW: "blue",
    CAT_INPUT: "indigo",
    CAT_VARIABLES: "red",
    CAT_LISTS: "peach",
    CAT_ACTORS: "yellow",       # #109: placed-actor scene blocks
    CAT_OPERATORS: "green",
    CAT_SOUND: "pink",
    CAT_PROCS: "dark_purple",   # #48: custom blocks (My Blocks / procedures)
}


# ============================================================================
# Block shapes
# ============================================================================

SHAPE_HAT = "hat"          # an event hat (top of a script): on_start/update/draw
SHAPE_STATEMENT = "stmt"   # a plain one-line statement (no body)
SHAPE_CBLOCK = "c-block"   # wraps a body of child statements ("c")
SHAPE_EXPR = "expr"        # a reporter/boolean expression (used in expr slots)
SHAPE_DEF = "def"          # #48: a custom-block DEFINITION hat (define NAME ...), a
                           # body owner like a hat but DELETABLE (removes the whole
                           # proc) -- lives in program["procs"], not program["scripts"].


def _slot(name, kind, **extra):
    s = {"name": name, "type": kind}
    for k in extra:
        s[k] = extra[k]
    return s


# ============================================================================
# THE VOCABULARY CATALOG
# ============================================================================
#
# Each entry: id -> {
#   "category": one of CAT_*,
#   "shape":    one of SHAPE_*,
#   "label":    a human template; "{slot}" placeholders name the param slots,
#               so Part 2 can render the block inline (Scratch-style),
#   "slots":    ordered list of slot descriptors (name + type + extras),
#   "emit":     how this block becomes code (see _emit_* in the compiler):
#                 statements/c-blocks have an "emit" key naming the emitter,
#                 expression blocks have an "expr" key with a code template.
# }
#
# Expression entries carry "expr": a template string with "{slot}" holes filled
# by the rendered sub-expressions/literals (e.g. op_add -> "({a} + {b})").
# Statement entries carry "emit": a code template (single line) similarly filled
# (e.g. cls -> "cls({color})"); c-block entries carry a special emitter name.

CATALOG = {

    # -- events (the cart lifecycle hats) ------------------------------------
    "on_start": {
        "category": CAT_EVENTS, "shape": SHAPE_HAT,
        "label": "when program starts",
        "slots": [], "lifecycle": "_init",
    },
    "on_update": {
        "category": CAT_EVENTS, "shape": SHAPE_HAT,
        "label": "every frame (update)",
        "slots": [], "lifecycle": "_update",
    },
    "on_draw": {
        "category": CAT_EVENTS, "shape": SHAPE_HAT,
        "label": "every frame (draw)",
        "slots": [], "lifecycle": "_draw",
    },

    # -- control -------------------------------------------------------------
    "if": {
        "category": CAT_CONTROL, "shape": SHAPE_CBLOCK,
        "label": "if {cond}",
        "slots": [_slot("cond", SLOT_EXPR)],
        "emit": "if",
    },
    "if_else": {
        "category": CAT_CONTROL, "shape": SHAPE_CBLOCK,
        "label": "if {cond} else",
        "slots": [_slot("cond", SLOT_EXPR)],
        "emit": "if_else",   # children split on the "else" marker block
    },
    "repeat": {
        "category": CAT_CONTROL, "shape": SHAPE_CBLOCK,
        "label": "repeat {times}",
        "slots": [_slot("times", SLOT_NUMBER, default=10)],
        "emit": "repeat",
    },
    "forever": {
        "category": CAT_CONTROL, "shape": SHAPE_CBLOCK,
        "label": "forever",
        "slots": [],
        "emit": "forever",   # NOTE: emits a bounded loop on-cart (see compiler)
    },
    "wait": {
        "category": CAT_CONTROL, "shape": SHAPE_STATEMENT,
        "label": "wait {secs} seconds",
        "slots": [_slot("secs", SLOT_NUMBER, default=1)],
        "emit": "_wait({secs})",
    },
    "repeat_until": {
        "category": CAT_CONTROL, "shape": SHAPE_CBLOCK,
        "label": "repeat until {cond}",
        "slots": [_slot("cond", SLOT_EXPR)],
        "emit": "repeat_until",   # NOTE: emits a bounded loop on-cart (see compiler)
    },
    "wait_until": {
        "category": CAT_CONTROL, "shape": SHAPE_STATEMENT,
        "label": "wait until {cond}",
        "slots": [_slot("cond", SLOT_EXPR)],
        # frame loop: a true block-until can't run (it would freeze the console), so
        # this is a documented no-op -- the cart's own _update keeps polling each
        # frame, which is how a kid actually waits for a condition (see _HELPER_WAIT_UNTIL).
        "emit": "_wait_until({cond})",
    },
    "stop": {
        "category": CAT_CONTROL, "shape": SHAPE_STATEMENT,
        "label": "stop this script",
        "slots": [],
        "emit": "return",   # returns from the current lifecycle function (always valid)
    },
    "break_loop": {
        "category": CAT_CONTROL, "shape": SHAPE_STATEMENT,
        "label": "break out of loop",
        "slots": [],
        "emit": "break",    # only emitted inside a loop; a stray one becomes `pass`
    },

    # -- draw ----------------------------------------------------------------
    "cls": {
        "category": CAT_DRAW, "shape": SHAPE_STATEMENT,
        "label": "clear screen to {color}",
        "slots": [_slot("color", SLOT_DROPDOWN, options="COLORS", default="black")],
        "emit": "cls(col({color}))",
    },
    "spr": {
        "category": CAT_DRAW, "shape": SHAPE_STATEMENT,
        "label": "draw sprite {id} at x {x} y {y}",
        "slots": [_slot("id", SLOT_NUMBER, default=0),
                  _slot("x", SLOT_EXPR), _slot("y", SLOT_EXPR)],
        "emit": "spr({id}, {x}, {y})",
    },
    "print": {
        "category": CAT_DRAW, "shape": SHAPE_STATEMENT,
        "label": "write {text} at x {x} y {y} in {color}",
        "slots": [_slot("text", SLOT_EXPR), _slot("x", SLOT_EXPR), _slot("y", SLOT_EXPR),
                  _slot("color", SLOT_DROPDOWN, options="COLORS", default="white")],
        "emit": "print({text}, {x}, {y}, col({color}))",
    },
    "rect": {
        "category": CAT_DRAW, "shape": SHAPE_STATEMENT,
        "label": "fill box at {x},{y} size {w}x{h} in {color}",
        "slots": [_slot("x", SLOT_EXPR), _slot("y", SLOT_EXPR),
                  _slot("w", SLOT_NUMBER, default=16), _slot("h", SLOT_NUMBER, default=16),
                  _slot("color", SLOT_DROPDOWN, options="COLORS", default="white")],
        "emit": "rect({x}, {y}, {w}, {h}, col({color}))",
    },
    "rectb": {
        "category": CAT_DRAW, "shape": SHAPE_STATEMENT,
        "label": "outline box at {x},{y} size {w}x{h} in {color}",
        "slots": [_slot("x", SLOT_EXPR), _slot("y", SLOT_EXPR),
                  _slot("w", SLOT_NUMBER, default=16), _slot("h", SLOT_NUMBER, default=16),
                  _slot("color", SLOT_DROPDOWN, options="COLORS", default="white")],
        "emit": "rectb({x}, {y}, {w}, {h}, col({color}))",
    },
    "circ": {
        "category": CAT_DRAW, "shape": SHAPE_STATEMENT,
        "label": "fill circle at {x},{y} radius {r} in {color}",
        "slots": [_slot("x", SLOT_EXPR), _slot("y", SLOT_EXPR),
                  _slot("r", SLOT_NUMBER, default=8),
                  _slot("color", SLOT_DROPDOWN, options="COLORS", default="white")],
        "emit": "circ({x}, {y}, {r}, col({color}))",
    },
    "line": {
        "category": CAT_DRAW, "shape": SHAPE_STATEMENT,
        "label": "line {x0},{y0} to {x1},{y1} in {color}",
        "slots": [_slot("x0", SLOT_EXPR), _slot("y0", SLOT_EXPR),
                  _slot("x1", SLOT_EXPR), _slot("y1", SLOT_EXPR),
                  _slot("color", SLOT_DROPDOWN, options="COLORS", default="white")],
        "emit": "line({x0}, {y0}, {x1}, {y1}, col({color}))",
    },
    "pix": {
        "category": CAT_DRAW, "shape": SHAPE_STATEMENT,
        "label": "dot at {x},{y} in {color}",
        "slots": [_slot("x", SLOT_EXPR), _slot("y", SLOT_EXPR),
                  _slot("color", SLOT_DROPDOWN, options="COLORS", default="white")],
        "emit": "pix({x}, {y}, col({color}))",
    },

    # -- input (expression readers) ------------------------------------------
    "btn": {
        "category": CAT_INPUT, "shape": SHAPE_EXPR,
        "label": "button {dir} held",
        "slots": [_slot("dir", SLOT_DROPDOWN, options="BUTTONS", default="left")],
        "expr": "btn({dir})",
    },
    "btnp": {
        "category": CAT_INPUT, "shape": SHAPE_EXPR,
        "label": "button {dir} pressed",
        "slots": [_slot("dir", SLOT_DROPDOWN, options="BUTTONS", default="a")],
        "expr": "btnp({dir})",
    },
    "touched": {
        "category": CAT_INPUT, "shape": SHAPE_EXPR,
        "label": "screen tapped",
        "slots": [],
        "expr": "_touched()",   # helper: True on a tap this frame
    },
    "touch_x": {
        "category": CAT_INPUT, "shape": SHAPE_EXPR,
        "label": "tap x",
        "slots": [],
        "expr": "_touch_x()",   # helper: x of the last tap (so a game can hit-test)
    },
    "touch_y": {
        "category": CAT_INPUT, "shape": SHAPE_EXPR,
        "label": "tap y",
        "slots": [],
        "expr": "_touch_y()",   # helper: y of the last tap
    },

    # -- variables -----------------------------------------------------------
    "set_var": {
        "category": CAT_VARIABLES, "shape": SHAPE_STATEMENT,
        "label": "set {var} to {value}",
        "slots": [_slot("var", SLOT_VARIABLE), _slot("value", SLOT_EXPR)],
        "emit": "{var} = {value}",
    },
    "change_var": {
        "category": CAT_VARIABLES, "shape": SHAPE_STATEMENT,
        "label": "change {var} by {value}",
        "slots": [_slot("var", SLOT_VARIABLE), _slot("value", SLOT_EXPR)],
        "emit": "{var} = {var} + ({value})",
    },
    "var": {
        "category": CAT_VARIABLES, "shape": SHAPE_EXPR,
        "label": "{var}",
        "slots": [_slot("var", SLOT_VARIABLE)],
        "expr": "{var}",
    },

    # -- operators (expression blocks) ---------------------------------------
    "op_add": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "{a} + {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "({a} + {b})",
    },
    "op_sub": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "{a} - {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "({a} - {b})",
    },
    "op_mul": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "{a} x {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "({a} * {b})",
    },
    "op_div": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "{a} / {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "({a} / {b})",
    },
    "op_eq": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "{a} = {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "({a} == {b})",
    },
    "op_lt": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "{a} < {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "({a} < {b})",
    },
    "op_gt": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "{a} > {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "({a} > {b})",
    },
    "op_and": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "{a} and {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "({a} and {b})",
    },
    "op_or": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "{a} or {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "({a} or {b})",
    },
    "op_not": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "not {a}",
        "slots": [_slot("a", SLOT_EXPR)], "expr": "(not {a})",
    },
    "op_rnd": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "random to {n}",
        # a whole number 0..n-1 (int(rnd) -- a kid expects "random to 10" to give a
        # countable number, not 7.34; perfect for a random screen position too).
        "slots": [_slot("n", SLOT_EXPR, default=10)], "expr": "int(rnd({n}))",
    },
    # -- more operators (#48): comparisons + math reporters ------------------
    "op_le": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "{a} <= {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "({a} <= {b})",
    },
    "op_ge": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "{a} >= {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "({a} >= {b})",
    },
    "op_ne": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "{a} != {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "({a} != {b})",
    },
    "op_mod": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "{a} mod {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "({a} % {b})",
    },
    "op_round": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "round {a}",
        "slots": [_slot("a", SLOT_EXPR)], "expr": "round({a})",
    },
    "op_abs": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "abs {a}",
        "slots": [_slot("a", SLOT_EXPR)], "expr": "abs({a})",
    },
    "op_min": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "min of {a} and {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "min({a}, {b})",
    },
    "op_max": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "max of {a} and {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)], "expr": "max({a}, {b})",
    },
    "op_sqrt": {
        # sqrt without importing math (carts import nothing): x ** 0.5 is portable.
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "sqrt {a}",
        "slots": [_slot("a", SLOT_EXPR)], "expr": "(({a}) ** 0.5)",
    },
    # simple string ops (clean, no helpers needed except a safe "letter of")
    "op_join": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "join {a} {b}",
        "slots": [_slot("a", SLOT_EXPR), _slot("b", SLOT_EXPR)],
        "expr": "(str({a}) + str({b}))",
    },
    "op_text_len": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "length of text {a}",
        "slots": [_slot("a", SLOT_EXPR)], "expr": "len(str({a}))",
    },
    "op_letter": {
        "category": CAT_OPERATORS, "shape": SHAPE_EXPR, "label": "letter {n} of {a}",
        # 1-based like Scratch; a safe helper returns "" when out of range.
        "slots": [_slot("n", SLOT_EXPR, default=1), _slot("a", SLOT_EXPR)],
        "expr": "_letter({a}, {n})",
    },

    # -- lists (#48): the multi-thing data type ------------------------------
    # Statements mutate a declared list; reporters read it; for_each iterates it.
    "list_add": {
        "category": CAT_LISTS, "shape": SHAPE_STATEMENT,
        "label": "add {item} to {list}",
        "slots": [_slot("item", SLOT_EXPR), _slot("list", SLOT_LIST)],
        "emit": "{list}.append({item})",
    },
    "list_clear": {
        "category": CAT_LISTS, "shape": SHAPE_STATEMENT,
        "label": "clear {list}",
        "slots": [_slot("list", SLOT_LIST)],
        "emit": "{list} = []",          # reassigns -> the function gets `global {list}`
    },
    "list_remove_at": {
        "category": CAT_LISTS, "shape": SHAPE_STATEMENT,
        "label": "remove item {index} of {list}",
        # 1-based index; a safe helper ignores an out-of-range index (no crash).
        "slots": [_slot("index", SLOT_EXPR, default=1), _slot("list", SLOT_LIST)],
        "emit": "_lremove({list}, {index})",
    },
    "list_set_at": {
        "category": CAT_LISTS, "shape": SHAPE_STATEMENT,
        "label": "set item {index} of {list} to {item}",
        # 1-based; a safe helper ignores an out-of-range index.
        "slots": [_slot("index", SLOT_EXPR, default=1), _slot("list", SLOT_LIST),
                  _slot("item", SLOT_EXPR)],
        "emit": "_lset({list}, {index}, {item})",
    },
    "list_get": {
        "category": CAT_LISTS, "shape": SHAPE_EXPR,
        "label": "item {index} of {list}",
        # 1-based; the helper returns 0 for an out-of-range index (kid-safe).
        "slots": [_slot("index", SLOT_EXPR, default=1), _slot("list", SLOT_LIST)],
        "expr": "_lget({list}, {index})",
    },
    "list_len": {
        "category": CAT_LISTS, "shape": SHAPE_EXPR,
        "label": "length of {list}",
        "slots": [_slot("list", SLOT_LIST)],
        "expr": "len({list})",
    },
    "for_each": {
        "category": CAT_LISTS, "shape": SHAPE_CBLOCK,
        "label": "for each {var} in {list}",
        # `var` is a declared variable (the loop var); the body runs once per item.
        "slots": [_slot("var", SLOT_VARIABLE), _slot("list", SLOT_LIST)],
        "emit": "for_each",
    },

    # -- actors (#109): placed-actor scenes, the declarative game-object rung ------
    # These operate on the LIVE actor world make_api builds from the active scene
    # (widgets.SceneWorld): for_each_actor iterates the placed actors of a tag and
    # binds an implicit "current actor"; touching?/move/remove act on THAT current
    # actor (the innermost for-each's loop var, threaded through the compile ctx). A
    # move/remove/touching block outside any for-each-actor compiles against `None`,
    # which every verb treats as a safe no-op (the same "a stray block never breaks
    # the cart" discipline as break_loop). draw_scene draws every remaining actor.
    "for_each_actor": {
        "category": CAT_ACTORS, "shape": SHAPE_CBLOCK,
        "label": "for each {tag} actor",
        "slots": [_slot("tag", SLOT_TEXT, default="")],
        "emit": "for_each_actor",
    },
    "actor_touching": {
        "category": CAT_ACTORS, "shape": SHAPE_EXPR,
        "label": "actor touching {tag}?",
        # the current actor overlaps ANY live actor of tag {tag} (AABB, 8px boxes).
        "slots": [_slot("tag", SLOT_TEXT, default="")],
        "expr": "touching({__actor__}, {tag})",
    },
    "move_actor_by": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "move actor by {dx} {dy}",
        "slots": [_slot("dx", SLOT_EXPR, default=0), _slot("dy", SLOT_EXPR, default=0)],
        "emit": "move_actor({__actor__}, {dx}, {dy})",
    },
    "move_actor_to": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "move actor to {x} {y}",
        "slots": [_slot("x", SLOT_EXPR, default=0), _slot("y", SLOT_EXPR, default=0)],
        "emit": "move_actor_to({__actor__}, {x}, {y})",
    },
    "remove_actor": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "remove actor",
        "slots": [],
        "emit": "remove_actor({__actor__})",
    },
    "draw_scene": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "draw scene",
        "slots": [],
        "emit": "draw_scene()",
    },

    # -- sound ---------------------------------------------------------------
    "sfx": {
        "category": CAT_SOUND, "shape": SHAPE_STATEMENT,
        "label": "play sound {n}",
        "slots": [_slot("n", SLOT_NUMBER, default=0)],
        "emit": "sfx({n})",
    },
    "beep": {
        "category": CAT_SOUND, "shape": SHAPE_STATEMENT,
        "label": "beep at {freq} Hz",
        "slots": [_slot("freq", SLOT_NUMBER, default=440)],
        "emit": "beep({freq})",
    },

    # -- my blocks (#48): custom procedures with parameters ------------------
    # These two are SHAPE-only catalog entries (category + shape); their label and
    # slots are DYNAMIC and program-aware -- see block_label / block_slots. A
    # `proc_def` is a definition hat (its name + params live in its own `p` and it
    # lives in program["procs"]); a `call` invokes a proc by name with 0..N args.
    "proc_def": {
        "category": CAT_PROCS, "shape": SHAPE_DEF,
        "label": "define {name}",           # dynamic; block_label renders name+params
        "slots": [],                        # edited via the PROC menu, not slot carets
    },
    "call": {
        "category": CAT_PROCS, "shape": SHAPE_STATEMENT,
        "label": "call {name}",             # dynamic; block_label adds one hole per arg
        "slots": [],                        # dynamic expr slots, one per proc param
        # NOTE: no "emit" template -- calls compile through _emit_call (dynamic args +
        # the recursion guard), never the generic single-line emitter.
    },
}

# The custom-block node type ids (#48), named so callers don't string-literal them.
PROC_DEF = "proc_def"
CALL = "call"

# A pseudo-block that marks the boundary between the if and else bodies inside an
# "if_else" c-block's children. It is NOT in CATALOG (it never appears in a menu);
# it only ever lives inside an if_else's "c" list.
ELSE_MARKER = "else"

# Dropdown option sets referenced by slots (so the editor and compiler share them).
COLORS = ["black", "dark_blue", "dark_purple", "dark_green", "brown", "dark_grey",
          "light_grey", "white", "red", "orange", "yellow", "green", "blue",
          "indigo", "pink", "peach"]
BUTTONS = ["left", "right", "up", "down", "a", "b"]
OPTION_SETS = {"COLORS": COLORS, "BUTTONS": BUTTONS}


# ============================================================================
# Catalog query API (Part 2's palette/insert menu reads these)
# ============================================================================

def categories():
    """Ordered list of category ids (for the insert-menu tabs)."""
    return list(CATEGORY_ORDER)


def blocks_in_category(category):
    """Ordered list of block-type ids in a category (for the insert menu).
    Stable order = catalog declaration order within the category."""
    return [bid for bid in CATALOG if CATALOG[bid]["category"] == category]


def block_def(type_id):
    """The catalog entry for a block type id (or None if unknown)."""
    return CATALOG.get(type_id)


def slot_options(slot):
    """Resolve a dropdown slot's options list (it may name an OPTION_SETS key)."""
    opts = slot.get("options")
    if isinstance(opts, str):
        return list(OPTION_SETS.get(opts, []))
    return list(opts or [])


def is_expr(type_id):
    d = CATALOG.get(type_id)
    return bool(d) and d["shape"] == SHAPE_EXPR


def is_cblock(type_id):
    d = CATALOG.get(type_id)
    return bool(d) and d["shape"] == SHAPE_CBLOCK


def is_def(type_id):
    """True for the custom-block DEFINITION hat (#48). Like a hat it owns a body,
    but unlike a hat it's deletable (deleting it removes the whole proc)."""
    return type_id == PROC_DEF


# ============================================================================
# Custom blocks (#48): program-aware label + slots
# ============================================================================
#
# A proc's define-hat carries its name + params in its own `p`:
#     {"t":"proc_def","p":{"name":"draw_star","params":["px","py"]},"c":[...]}
# A call carries the target name + positional args (each an expr value):
#     {"t":"call","p":{"name":"draw_star","args":[3, mk("var",{"var":"n"})]}}
# Because procs are created at runtime, their labels/slots can't be static CATALOG
# entries -- these two helpers derive them from the live program instead.

def proc_name(block):
    """The name a proc_def defines / a call targets (or "" if absent)."""
    return str((block.get("p", {}) or {}).get("name", "") or "")


def proc_params(block):
    """The parameter-name list of a proc_def (a fresh list; [] if none)."""
    return list((block.get("p", {}) or {}).get("params", []) or [])


def call_args(block):
    """The positional arg-value list of a call (a fresh list; [] if none)."""
    return list((block.get("p", {}) or {}).get("args", []) or [])


def find_proc(program, name):
    """The proc_def block declaring `name`, or None."""
    for pd in program.get("procs", []) or []:
        if proc_name(pd) == name:
            return pd
    return None


def block_slots(program, block):
    """The slot descriptors for a block -- DYNAMIC for a call (one expr slot per
    parameter of the proc it targets), the static CATALOG slots otherwise. proc_def
    has no slots (its name/params are edited through the PROC menu, not slot carets)."""
    tid = block.get("t")
    if tid == CALL:
        pd = find_proc(program, proc_name(block))
        n = len(proc_params(pd)) if pd is not None else len(call_args(block))
        return [_slot("arg%d" % i, SLOT_EXPR) for i in range(n)]
    if tid == PROC_DEF:
        return []
    d = CATALOG.get(tid)
    return list(d["slots"]) if d else []


def block_label(program, block):
    """The human label template for a block -- DYNAMIC for proc_def / call, the
    static CATALOG label otherwise. proc_def reads 'define NAME p1 p2' (brace-free:
    params are edited via the PROC menu). call reads 'NAME {arg0} {arg1} ...' so the
    generic inline renderer fills each arg slot's value in place."""
    tid = block.get("t")
    if tid == PROC_DEF:
        nm = proc_name(block) or "block"
        params = proc_params(block)
        return "define " + nm + ((" " + " ".join(params)) if params else "")
    if tid == CALL:
        nm = proc_name(block) or "?"
        n = len(block_slots(program, block))
        holes = " ".join("{arg%d}" % i for i in range(n))
        return nm + ((" " + holes) if holes else "")
    d = CATALOG.get(tid)
    return d["label"] if d else str(tid)


# ============================================================================
# Block construction helpers (Part 2's tree edits build on these)
# ============================================================================

def make_block(type_id, params=None, children=None):
    """Build a block dict. Unfilled slots get their catalog default (or a neutral
    zero/empty), so a freshly-inserted block always compiles."""
    # Custom blocks (#48) carry non-slot payloads (name / params / args) that the
    # generic slot-fill path would drop, so build them explicitly.
    if type_id == PROC_DEF:
        params = params or {}
        return {"t": PROC_DEF,
                "p": {"name": str(params.get("name", "")),
                      "params": list(params.get("params", []) or [])},
                "c": list(children) if children is not None else []}
    if type_id == CALL:
        params = params or {}
        return {"t": CALL,
                "p": {"name": str(params.get("name", "")),
                      "args": list(params.get("args", []) or [])}}
    d = CATALOG.get(type_id)
    p = {}
    if d is not None:
        for slot in d["slots"]:
            name = slot["name"]
            if params is not None and name in params:
                p[name] = params[name]
            else:
                p[name] = _default_for(slot)
    elif params:
        p = dict(params)
    blk = {"t": type_id, "p": p}
    if children is not None:
        blk["c"] = list(children)
    elif d is not None and d["shape"] == SHAPE_CBLOCK:
        blk["c"] = []
    return blk


def _default_for(slot):
    if "default" in slot:
        return slot["default"]
    t = slot["type"]
    if t == SLOT_NUMBER:
        return 0
    if t == SLOT_TEXT:
        return ""
    if t == SLOT_VARIABLE:
        return ""
    if t == SLOT_LIST:
        return ""
    if t == SLOT_DROPDOWN:
        opts = slot_options(slot)
        return opts[0] if opts else ""
    if t == SLOT_EXPR:
        return 0          # an empty expr slot defaults to the literal 0
    return 0


def empty_program():
    """A new, valid (empty) program with the three lifecycle scripts present."""
    return {
        "vars": [],
        "scripts": [
            make_block("on_start"),
            make_block("on_update"),
            make_block("on_draw"),
        ],
    }


# ============================================================================
# THE COMPILER:  compile_blocks(program) -> str   (a cart main.py)
# ============================================================================

_INDENT = "    "

# The first line every block-compiled main.py carries. The block editor uses it
# to tell a block-authored cart (safe to overwrite on SAVE) from a hand-written
# code cart (whose main.py must NEVER be clobbered by an empty block program).
BLOCK_MARKER = "# Made with Moybyte blocks."


def is_block_authored_source(src):
    """True if `src` looks like it was emitted by compile_blocks (i.e. it carries
    the BLOCK_MARKER on its first line). Used to gate the block editor so opening
    BLOCKS on a hand-written-code cart can't silently replace its main.py."""
    if not src:
        return False
    return str(src).lstrip().startswith(BLOCK_MARKER)


class BlockError(Exception):
    """Raised when a program references an unknown block type (a corrupt
    blocks.json). The caller keeps the previous good main.py."""
    pass


def _is_number(v):
    # bool is an int subclass; treat it as a literal too (renders True/False).
    return isinstance(v, (int, float))


def _render_text_literal(s):
    """A safe double-quoted Python string literal (MicroPython-safe -- no
    f-strings). Escapes backslash, quote, newline, tab, CR."""
    out = '"'
    for ch in str(s):
        if ch == "\\":
            out += "\\\\"
        elif ch == '"':
            out += '\\"'
        elif ch == "\n":
            out += "\\n"
        elif ch == "\t":
            out += "\\t"
        elif ch == "\r":
            out += "\\r"
        else:
            out += ch
    return out + '"'


def _render_number(v):
    # ints print bare; floats keep their repr.
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, int):
        return str(v)
    return repr(float(v))


class _Ctx:
    """Compile context threaded through the emitters (#48). It carries the declared
    names (so a variable / list slot is validated against them, never code), the
    `assigned` map for `global` hoisting, and `loop_depth` so a `break out of loop`
    block only emits `break` when it really is inside a loop (a stray one becomes a
    safe `pass`, so the generated Python always parses)."""

    def __init__(self, known_vars, known_lists, assigned,
                 params=None, pinfo=None, current_proc=None):
        self.vars = known_vars
        self.lists = known_lists
        self.assigned = assigned        # name -> True (a function reassigns it -> global)
        self.loop_depth = 0
        # #109: the "current actor" a for_each_actor block binds -- the code name of
        # the innermost actor-loop var (e.g. "_actor2"), or None outside any such loop.
        # touching?/move/remove render against it; None compiles to a safe no-op verb.
        self.actor_var = None
        # -- custom blocks (#48) ------------------------------------------------
        # `params` are the local parameter names of the proc currently compiling
        # (locals: valid as variable slots INSIDE the body, but never `global`-hoisted).
        # `pinfo` (a _ProcInfo) carries the known proc names, each proc's param list,
        # and the call-graph reachability used by the recursion guard. `current_proc`
        # is the name of the proc being compiled (None for a lifecycle script).
        self.params = set(params or [])
        self.pinfo = pinfo
        self.current_proc = current_proc


def _render_value(value, slot, ctx):
    """Render a param VALUE for a slot into a code fragment.

    - expr slot: value is either a nested expression block (dict) or a literal.
    - number slot: a numeric literal.
    - text slot: a quoted string literal.
    - variable slot: a bare variable NAME (validated against ctx.vars).
    - list slot: a bare list NAME (validated against ctx.lists).
    - dropdown slot: a quoted option string (color/button name).
    """
    kind = slot["type"]
    if kind == SLOT_EXPR:
        return _render_expr(value, ctx)
    if kind == SLOT_NUMBER:
        if isinstance(value, dict):              # tolerate an expr in a number slot
            return _render_expr(value, ctx)
        if _is_number(value):
            return _render_number(value)
        # tolerate a numeric string ("10"); else fall back to 0
        try:
            return _render_number(float(value) if "." in str(value) else int(value))
        except (TypeError, ValueError):
            return "0"
    if kind == SLOT_TEXT:
        return _render_text_literal(value)
    if kind == SLOT_VARIABLE:
        name = str(value)
        # A proc's parameters are valid variable references INSIDE its body (#48) --
        # they're the function's locals -- so accept them alongside the declared globals.
        if name not in ctx.vars and name not in ctx.params:
            raise BlockError("unknown variable: " + name)
        return name
    if kind == SLOT_LIST:
        name = str(value)
        if name not in ctx.lists:
            raise BlockError("unknown list: " + name)
        return name
    if kind == SLOT_DROPDOWN:
        return _render_text_literal(value)
    return _render_number(0)


def _render_expr(value, ctx):
    """Render an EXPRESSION (an expr-slot value) to a code fragment.

    A literal number stays a number; a literal string is quoted; a dict is an
    expression block (operator / var / input reader) rendered from its template."""
    if value is None:
        return "0"
    if _is_number(value):
        return _render_number(value)
    if isinstance(value, str):
        # a bare string literal in an expr slot (rare -- mostly print text)
        return _render_text_literal(value)
    if not isinstance(value, dict):
        return "0"
    tid = value.get("t")
    d = CATALOG.get(tid)
    if d is None or d["shape"] != SHAPE_EXPR:
        raise BlockError("not an expression block: " + str(tid))
    template = d["expr"]
    params = value.get("p", {}) or {}
    filled = {}
    for slot in d["slots"]:
        name = slot["name"]
        filled[name] = _render_value(params.get(name, _default_for(slot)),
                                     slot, ctx)
    # #109: an actor reporter (actor_touching) references the current for_each_actor
    # loop var via {__actor__}; None (outside a for-each) makes touching() return False.
    filled["__actor__"] = ctx.actor_var if ctx.actor_var else "None"
    return _fill(template, filled)


def _fill(template, parts):
    """Substitute {name} holes in a template with parts[name]. Plain string
    replace (no str.format) so it's MicroPython-safe and never trips on the
    rendered code's own braces."""
    out = template
    for name in parts:
        out = out.replace("{" + name + "}", parts[name])
    return out


# How many iterations a "bounded" kid loop (forever / repeat until) runs before it
# gives up -- big enough to feel endless within a frame, small enough never to hang
# the single-threaded console loop.
_LOOP_CAP = 100000

# Statements that REASSIGN a name (vs. mutate it in place), so the function that
# contains them must hoist that name with `global`. set/change write a variable;
# list_clear rebinds a list (name = []). The in-place list ops (.append, _lset,
# _lremove) mutate the existing object, so they need NO global.
_REASSIGN_VAR = ("set_var", "change_var")


def _emit_statement(block, ctx, indent, lines):
    """Append the line(s) for one statement/c-block at `indent` to `lines`.
    Records any name a statement reassigns into ctx.assigned (for global hoisting)."""
    tid = block.get("t")
    d = CATALOG.get(tid)
    if d is None:
        raise BlockError("unknown block: " + str(tid))
    shape = d["shape"]
    pad = _INDENT * indent
    params = block.get("p", {}) or {}

    # A custom-block CALL (#48) compiles through its own emitter (dynamic positional
    # args + the recursion guard), never the generic single-line template.
    if tid == CALL:
        _emit_call(block, ctx, pad, lines)
        return

    if shape == SHAPE_STATEMENT:
        # reassigning statements: record the target for `global` hoisting.
        if tid in _REASSIGN_VAR:
            ctx.assigned[str(params.get("var", ""))] = True
        elif tid == "list_clear":
            ctx.assigned[str(params.get("list", ""))] = True
        # `break out of loop` outside any loop would be a SyntaxError -- degrade it to
        # a harmless `pass` so a misplaced block never makes the cart fail to compile.
        if tid == "break_loop" and ctx.loop_depth <= 0:
            lines.append(pad + "pass")
            return
        filled = {}
        for slot in d["slots"]:
            name = slot["name"]
            filled[name] = _render_value(params.get(name, _default_for(slot)),
                                         slot, ctx)
        # #109: actor statements (move/remove) reference the current for_each_actor
        # loop var via {__actor__}; None (outside a for-each) is a safe no-op verb arg.
        filled["__actor__"] = ctx.actor_var if ctx.actor_var else "None"
        lines.append(pad + _fill(d["emit"], filled))
        return

    if shape == SHAPE_CBLOCK:
        _emit_cblock(tid, d, block, ctx, indent, lines)
        return

    # an expression block used where a statement is expected -- malformed program.
    raise BlockError("expression block used as a statement: " + str(tid))


def _emit_body(children, ctx, indent, lines):
    """Emit a list of child statements at `indent`. Empty bodies get `pass` so the
    generated Python always parses."""
    real = [c for c in children if c.get("t") != ELSE_MARKER]
    if not real:
        lines.append(_INDENT * indent + "pass")
        return
    for child in real:
        _emit_statement(child, ctx, indent, lines)


def _emit_loop_body(children, ctx, indent, lines):
    """Emit a loop body, tracking loop nesting so a `break out of loop` inside it
    emits a real `break` (and one outside any loop degrades to `pass`)."""
    ctx.loop_depth += 1
    _emit_body(children, ctx, indent, lines)
    ctx.loop_depth -= 1


def _emit_cblock(tid, d, block, ctx, indent, lines):
    pad = _INDENT * indent
    children = block.get("c", []) or []
    params = block.get("p", {}) or {}
    emit = d["emit"]

    if emit == "if":
        cond = _render_expr(params.get("cond", 0), ctx)
        lines.append(pad + "if " + cond + ":")
        _emit_body(children, ctx, indent + 1, lines)
        return

    if emit == "if_else":
        cond = _render_expr(params.get("cond", 0), ctx)
        # children are split into the if-body and the else-body on the ELSE_MARKER.
        if_body, else_body, seen_else = [], [], False
        for c in children:
            if c.get("t") == ELSE_MARKER:
                seen_else = True
                continue
            (else_body if seen_else else if_body).append(c)
        lines.append(pad + "if " + cond + ":")
        _emit_body(if_body, ctx, indent + 1, lines)
        lines.append(pad + "else:")
        _emit_body(else_body, ctx, indent + 1, lines)
        return

    if emit == "repeat":
        times = _render_value(params.get("times", 10),
                              {"name": "times", "type": SLOT_NUMBER}, ctx)
        # int() guards a float count; the loop var is namespaced so nested repeats
        # never collide and it can't clash with a kid's variable.
        lvar = "_i%d" % indent
        lines.append(pad + "for " + lvar + " in range(int(" + times + ")):")
        _emit_loop_body(children, ctx, indent + 1, lines)
        return

    if emit == "forever":
        # "forever" can't be a true `while True:` on a single-threaded frame loop
        # (it would hang the console), so it emits a generous bounded loop -- the
        # body still runs "forever" from the kid's point of view within a frame,
        # and the cart's own _update/_draw is what actually repeats each frame.
        lvar = "_i%d" % indent
        lines.append(pad + "for " + lvar + " in range(%d):" % _LOOP_CAP)
        _emit_loop_body(children, ctx, indent + 1, lines)
        return

    if emit == "repeat_until":
        # "repeat until cond" -- like forever, it must stay bounded (a kid can write a
        # condition that never becomes true, and a true `while` would hang the frame).
        # So: a bounded loop that breaks as soon as the condition holds, checked at the
        # TOP each pass (do-nothing-if-already-true, like Scratch's repeat-until).
        cond = _render_expr(params.get("cond", 0), ctx)
        lvar = "_i%d" % indent
        lines.append(pad + "for " + lvar + " in range(%d):" % _LOOP_CAP)
        lines.append(pad + _INDENT + "if " + cond + ":")
        lines.append(pad + _INDENT + _INDENT + "break")
        _emit_loop_body(children, ctx, indent + 1, lines)
        return

    if emit == "for_each":
        # iterate a declared list, binding each item to a declared variable. The loop
        # var is assigned -> the function hoists it `global` (like set_var).
        vname = _render_value(params.get("var", ""),
                              {"name": "var", "type": SLOT_VARIABLE}, ctx)
        lname = _render_value(params.get("list", ""),
                              {"name": "list", "type": SLOT_LIST}, ctx)
        ctx.assigned[vname] = True
        lines.append(pad + "for " + vname + " in " + lname + ":")
        _emit_loop_body(children, ctx, indent + 1, lines)
        return

    if emit == "for_each_actor":
        # #109: iterate the placed actors of a tag over the live scene world. The loop
        # var is the "current actor" its body's touching?/move/remove act on -- threaded
        # through ctx.actor_var (restored on the way out so sibling/outer scopes are
        # unaffected). actors(tag) hands back a SNAPSHOT, so a body that remove_actor()s
        # the current item never skips the next (the remove-while-iterating trap is the
        # verb's problem, never the kid's). The var is namespaced by indent so nested
        # actor loops don't collide and can't clash with a kid's variable.
        tag = _render_value(params.get("tag", ""),
                            {"name": "tag", "type": SLOT_TEXT}, ctx)
        avar = "_actor%d" % indent
        lines.append(pad + "for " + avar + " in actors(" + tag + "):")
        prev = ctx.actor_var
        ctx.actor_var = avar
        _emit_loop_body(children, ctx, indent + 1, lines)
        ctx.actor_var = prev
        return

    raise BlockError("unknown c-block emitter: " + str(emit))


def _emit_call(block, ctx, pad, lines):
    """Emit a custom-block CALL (#48): `name(arg0, arg1, ...)`.

    Degrades to `pass` (matching the stray-break philosophy) when the call can't be
    emitted safely:
      * the target proc doesn't exist (a call to a deleted definition), or
      * emitting it would create a call CYCLE -- a direct self-call or mutual
        recursion. A synchronous frame loop can't recurse unbounded, so the
        cycle-closing call becomes a no-op instead of a hang (the documented guard).
    Args render positionally to the proc's CURRENT parameter count: extra stored args
    are ignored and missing ones default to 0, so a call stays valid even after its
    proc gains or loses a parameter."""
    pinfo = ctx.pinfo
    name = proc_name(block)
    if pinfo is None or name not in pinfo.names:
        lines.append(pad + "pass")               # unknown / deleted proc -> no-op
        return
    if _call_would_recurse(ctx, name):
        lines.append(pad + "pass")               # direct or mutual recursion -> no-op
        return
    nparams = len(pinfo.params.get(name, []))
    args = call_args(block)
    parts = []
    for i in range(nparams):
        val = args[i] if i < len(args) else 0
        parts.append(_render_expr(val, ctx))
    lines.append(pad + name + "(" + ", ".join(parts) + ")")


def _call_would_recurse(ctx, target):
    """True if a call to `target` from the proc currently compiling would close a
    call cycle (direct self-call, or mutual recursion where `target` can reach back
    to the caller). Lifecycle scripts (current_proc is None) never recurse."""
    cur = ctx.current_proc
    if cur is None or ctx.pinfo is None:
        return False
    if target == cur:
        return True
    return cur in ctx.pinfo.reach.get(target, ())


# Helper snippets the generated cart needs (emitted once, only when referenced).
_HELPER_TOUCHED = (
    "def _touched():\n"
    "    t = touch()\n"
    "    return bool(t) and t[2]\n"
)
# tap position readers: the x / y of the pointer this frame, or -100 (well off the
# 320x240 screen) when there's no pointer -- so a hit-test against a target never
# matches when nothing is touched.
_HELPER_TOUCH_X = (
    "def _touch_x():\n"
    "    t = touch()\n"
    "    return t[0] if t else -100\n"
)
_HELPER_TOUCH_Y = (
    "def _touch_y():\n"
    "    t = touch()\n"
    "    return t[1] if t else -100\n"
)
_HELPER_WAIT = (
    "def _wait(_secs):\n"
    "    pass\n"          # frame-based runtime: a real sleep would stall the loop
)
_HELPER_WAIT_UNTIL = (
    "def _wait_until(_cond):\n"
    "    pass\n"          # frame loop: can't block; the cart's _update re-checks each frame
)
# List helpers (#48). 1-based indexing (kid-friendly, Scratch-style) and bounds-safe
# so a bad index never crashes the cart: get returns 0, remove/set ignore it.
_HELPER_LGET = (
    "def _lget(_lst, _i):\n"
    "    _i = int(_i) - 1\n"
    "    if 0 <= _i < len(_lst):\n"
    "        return _lst[_i]\n"
    "    return 0\n"
)
_HELPER_LREMOVE = (
    "def _lremove(_lst, _i):\n"
    "    _i = int(_i) - 1\n"
    "    if 0 <= _i < len(_lst):\n"
    "        del _lst[_i]\n"
)
_HELPER_LSET = (
    "def _lset(_lst, _i, _v):\n"
    "    _i = int(_i) - 1\n"
    "    if 0 <= _i < len(_lst):\n"
    "        _lst[_i] = _v\n"
)
# "letter N of text" -- 1-based, returns "" when out of range (kid-safe).
_HELPER_LETTER = (
    "def _letter(_s, _n):\n"
    "    _s = str(_s)\n"
    "    _n = int(_n) - 1\n"
    "    if 0 <= _n < len(_s):\n"
    "        return _s[_n]\n"
    "    return \"\"\n"
)


def _uses(trees, needle):
    """True if any block tree in `trees` (scripts and/or proc defs) contains a block
    whose type id == needle. Walks child bodies ("c"), nested expression params ("p"),
    and list-valued params such as a call's positional args (#48)."""
    found = [False]

    def walk(node):
        if isinstance(node, list):                # e.g. a call's positional args (#48)
            for it in node:
                walk(it)
            return
        if not isinstance(node, dict):
            return
        if node.get("t") == needle:
            found[0] = True
        for c in node.get("c", []) or []:
            walk(c)
        for v in (node.get("p", {}) or {}).values():
            walk(v)

    for s in trees:
        walk(s)
    return found[0]


def collect_vars(program):
    """The declared variable names, de-duplicated and order-preserving. Names are
    validated to be safe Python identifiers (the editor enforces this; we guard so
    a hand-edited blocks.json can't inject code through a variable slot)."""
    out = []
    seen = {}
    for raw in program.get("vars", []) or []:
        name = str(raw)
        if not _is_identifier(name):
            raise BlockError("bad variable name: " + name)
        if name not in seen:
            seen[name] = True
            out.append(name)
    return out


def collect_lists(program):
    """The declared list names (#48), de-duplicated and order-preserving, each a safe
    Python identifier. A list and a variable can't share a name (both are globals)."""
    var_set = set(collect_vars(program))
    out = []
    seen = {}
    for raw in program.get("lists", []) or []:
        name = str(raw)
        if not _is_identifier(name):
            raise BlockError("bad list name: " + name)
        if name in var_set:
            raise BlockError("list name clashes with a variable: " + name)
        if name not in seen:
            seen[name] = True
            out.append(name)
    return out


_KEYWORDS = {
    "False", "None", "True", "and", "as", "assert", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "finally", "for", "from", "global",
    "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise",
    "return", "try", "while", "with", "yield",
}

# Names a custom block (#48) may NOT take: a proc compiles to `def <name>(...)`, so
# a name that collides with a cart-API verb (the make_api namespace), a builtin the
# generated code calls, a compiler helper, or a lifecycle function would SHADOW it
# and break the cart. The editor gates proc names through this too, so a BlockError
# here only ever fires on a hand-corrupted blocks.json.
_RESERVED_NAMES = {
    # cart API verbs (host_app.make_api / moy_runtime -- keep in sync if that grows)
    "W", "H", "cls", "pix", "line", "rect", "rectb", "circ", "circb", "spr",
    "spr_batch", "background", "make_layer", "draw_layer", "map", "mget", "mset",
    "print", "touch", "mouse", "clip", "camera", "pal", "palt", "btn", "btnp",
    "key", "keyp", "time", "pmem", "textmode", "quit", "cfg", "col", "sfx", "beep",
    "music", "music_stop", "sound_stop", "volume", "rnd", "flr", "Image", "image",
    "wifi", "scene", "load_scene", "table", "text",   # #85 scenes + #78 Desk Lab interop
    "actors", "touching", "move_actor", "move_actor_to",  # #109 actor-aware verbs
    "remove_actor", "draw_scene",
    # builtins the generated code relies on
    "int", "range", "len", "str", "min", "max", "abs", "round", "bool",
    # lifecycle functions + the compiler's own helpers (all `_`-prefixed)
    "_init", "_update", "_draw", "_touched", "_touch_x", "_touch_y", "_wait",
    "_wait_until", "_lget", "_lremove", "_lset", "_letter",
}


def is_reserved_name(name):
    """True if `name` is a reserved verb/builtin/helper a custom block can't reuse."""
    return name in _RESERVED_NAMES


class _ProcInfo:
    """Compiled facts about a program's custom blocks (#48), built once per compile:
    the set of proc names, each proc's parameter list, and the call-graph
    reachability the recursion guard consults (reach[a] = every proc transitively
    callable starting from a)."""

    def __init__(self, names, params, reach):
        self.names = names          # set of proc names
        self.params = params        # name -> [param, ...]
        self.reach = reach          # name -> set of transitively-reachable proc names


def collect_procs(program):
    """The declared custom blocks (#48) as a list of proc_def blocks, validated.

    Each proc becomes a top-level `def name(params):`, so names/params are held to
    the same identifier + collision discipline as variables/lists (a hand-edited
    blocks.json can't inject code or shadow the cart API through them):
      * name is a safe identifier, not a reserved verb/builtin/helper, unique among
        procs, and doesn't clash with a declared variable or list (all module-level),
      * each parameter is a safe identifier, unique within that proc, and not reserved.
    Returns [] for a pre-#48 program with no "procs" key (back-compat)."""
    var_set = set(collect_vars(program))
    list_set = set(collect_lists(program))
    out = []
    seen = {}
    for pd in program.get("procs", []) or []:
        if not isinstance(pd, dict) or pd.get("t") != PROC_DEF:
            raise BlockError("not a custom-block definition")
        name = proc_name(pd)
        if not _is_identifier(name):
            raise BlockError("bad custom-block name: " + name)
        if is_reserved_name(name):
            raise BlockError("custom-block name is reserved: " + name)
        if name in seen:
            raise BlockError("duplicate custom-block name: " + name)
        if name in var_set or name in list_set:
            raise BlockError("custom-block name clashes with a variable/list: " + name)
        pseen = {}
        for raw in proc_params(pd):
            pn = str(raw)
            if not _is_identifier(pn):
                raise BlockError("bad parameter name: " + pn)
            if is_reserved_name(pn):
                raise BlockError("parameter name is reserved: " + pn)
            if pn in pseen:
                raise BlockError("duplicate parameter name: " + pn)
            pseen[pn] = True
        seen[name] = True
        out.append(pd)
    return out


def _proc_info(procs):
    """Build the _ProcInfo (names / params / call-graph reachability) for `procs`."""
    names = set(proc_name(pd) for pd in procs)
    params = {}
    edges = {}
    for pd in procs:
        nm = proc_name(pd)
        params[nm] = proc_params(pd)
        edges[nm] = _calls_in(pd.get("c", []) or [], names)
    reach = {}
    for nm in names:
        reach[nm] = _reachable_from(nm, edges)
    return _ProcInfo(names, params, reach)


def _calls_in(body, names):
    """The set of proc names this body (a statement/expr tree) directly calls."""
    out = set()

    def walk(node):
        if isinstance(node, list):
            for it in node:
                walk(it)
            return
        if not isinstance(node, dict):
            return
        if node.get("t") == CALL:
            tgt = proc_name(node)
            if tgt in names:
                out.add(tgt)
        for c in node.get("c", []) or []:
            walk(c)
        for v in (node.get("p", {}) or {}).values():
            walk(v)

    for b in body:
        walk(b)
    return out


def _reachable_from(start, edges):
    """Every proc name transitively reachable by following call edges from `start`
    (NOT including `start` unless a cycle leads back to it)."""
    reach = set()
    stack = list(edges.get(start, ()))
    while stack:
        nm = stack.pop()
        if nm in reach:
            continue
        reach.add(nm)
        for nxt in edges.get(nm, ()):
            if nxt not in reach:
                stack.append(nxt)
    return reach


def _is_identifier(name):
    if not name:
        return False
    if name in _KEYWORDS:
        return False
    first = name[0]
    if not (first.isalpha() or first == "_"):
        return False
    for ch in name[1:]:
        if not (ch.isalpha() or ch.isdigit() or ch == "_"):
            return False
    return True


def is_identifier(name):
    """Public alias for the variable-name validator (the block editor's create/
    rename flow gates names through this so a slot can never inject code)."""
    return _is_identifier(name)


def sanitize_var_name(raw):
    """Coerce a kid's free-typed text into a SAFE Python identifier for a variable
    name (MicroPython-safe, no regex): keep letters/digits/underscore, turn spaces
    and other characters into underscores, prefix an underscore if it starts with a
    digit, and avoid clashing with a keyword. Returns "" if nothing usable remains
    (the caller then falls back to a generated default)."""
    out = ""
    for ch in str(raw).strip():
        if ch.isalpha() or ch.isdigit() or ch == "_":
            out += ch
        elif ch == " " or ch == "-":
            out += "_"
        # anything else (quotes, parens, punctuation) is dropped
    if not out:
        return ""
    if out[0].isdigit():
        out = "_" + out
    if out in _KEYWORDS:
        out = out + "_"
    return out


def unique_var_name(existing, base="var"):
    """A variable name not already in `existing`: `base`, else base2, base3, ...
    Used to seed a freshly-created variable with a sensible default the kid can
    then rename."""
    existing = set(existing or [])
    base = sanitize_var_name(base) or "var"
    if base not in existing:
        return base
    i = 2
    while (base + str(i)) in existing:
        i += 1
    return base + str(i)


def parse_number_literal(raw, default=0):
    """Coerce a kid's free-typed text into a STORED numeric literal (int or float)
    for a number / expr-literal slot. The block editor types into a text buffer; on
    commit it runs that buffer through here so a slot ALWAYS holds a real number, not
    a string -- which the compiler then emits bare (`5`), never as code. Accepts a
    leading '-' and at most one '.', drops every other character (so a slot can never
    inject code), and falls back to `default` when nothing numeric remains.

    MicroPython-safe (no regex). '-3' -> -3, '4.5' -> 4.5, '1.2.3' -> 1.23 (the
    second '.' is dropped, its digits kept), 'x9' -> 9, '' / '-' / '.' -> default."""
    s = str(raw).strip()
    neg = False
    if s[:1] == "-":
        neg = True
        s = s[1:]
    digits = ""
    seen_dot = False
    for ch in s:
        if ch.isdigit():
            digits += ch
        elif ch == "." and not seen_dot:
            seen_dot = True
            digits += "."
        # everything else (letters, a second dot, punctuation) is dropped
    # nothing usable (empty, or just a "."): keep the caller's default
    if not digits or digits == ".":
        return default
    if "." in digits:
        try:
            val = float(digits)
        except (TypeError, ValueError):
            return default
    else:
        try:
            val = int(digits)
        except (TypeError, ValueError):
            return default
    return -val if neg else val


def is_literal_value(value):
    """True if an expr-slot value is a typed LITERAL (number/string/None) rather
    than a nested reporter block (a dict). The editor uses this to decide whether a
    slot is currently editable-as-text or holds a block."""
    return not isinstance(value, dict)


def compile_blocks(program):
    """Compile a block program (the schema above) into a cart's main.py source.

    Returns a string of valid, MicroPython-safe Python: module-level variable
    inits, then one lifecycle function (_init/_update/_draw) per present event
    script, with `global` hoisting for any variable a function assigns. Raises
    BlockError on a corrupt program (unknown block/variable) so the caller leaves
    the previous good main.py untouched."""
    if not isinstance(program, dict):
        raise BlockError("program is not a dict")
    known_vars = collect_vars(program)
    known_lists = collect_lists(program)
    procs = collect_procs(program)                 # #48: custom blocks (validated)
    pinfo = _proc_info(procs)
    scripts = program.get("scripts", []) or []
    # Helper detection must see proc bodies too (a helper may be used only inside a
    # custom block), so scan scripts + proc definitions together.
    use_trees = scripts + procs

    out = [BLOCK_MARKER + " Edit in the block editor (or graduate to code)."]

    # module-level declarations: variables start at 0, lists start empty.
    if known_vars or known_lists:
        out.append("")
        for v in known_vars:
            out.append(v + " = 0")
        for lst in known_lists:
            out.append(lst + " = []")

    # helper functions, emitted only when used (keeps the cart minimal + readable).
    if _uses(use_trees, "touched"):
        out.append("")
        out.append(_HELPER_TOUCHED.rstrip("\n"))
    if _uses(use_trees, "touch_x"):
        out.append("")
        out.append(_HELPER_TOUCH_X.rstrip("\n"))
    if _uses(use_trees, "touch_y"):
        out.append("")
        out.append(_HELPER_TOUCH_Y.rstrip("\n"))
    if _uses(use_trees, "wait"):
        out.append("")
        out.append(_HELPER_WAIT.rstrip("\n"))
    if _uses(use_trees, "wait_until"):
        out.append("")
        out.append(_HELPER_WAIT_UNTIL.rstrip("\n"))
    if _uses(use_trees, "list_get"):
        out.append("")
        out.append(_HELPER_LGET.rstrip("\n"))
    if _uses(use_trees, "list_remove_at"):
        out.append("")
        out.append(_HELPER_LREMOVE.rstrip("\n"))
    if _uses(use_trees, "list_set_at"):
        out.append("")
        out.append(_HELPER_LSET.rstrip("\n"))
    if _uses(use_trees, "op_letter"):
        out.append("")
        out.append(_HELPER_LETTER.rstrip("\n"))

    # custom-block definitions (#48): one top-level `def name(params):` per proc,
    # emitted BEFORE the lifecycle functions (Python resolves call names at call time,
    # so the order is purely cosmetic; procs-first reads cleaner). Each body compiles
    # with its params as LOCALS -- valid as variable references, never `global`-hoisted
    # -- while a declared var/list the body REASSIGNS is still hoisted `global`, exactly
    # like a lifecycle function. Emitting nothing when there are no procs keeps a
    # procs-less program byte-identical to pre-#48 output.
    for pd in procs:
        name = proc_name(pd)
        params = proc_params(pd)
        out.append("")
        out.append("")
        out.append("def " + name + "(" + ", ".join(params) + "):")
        body_lines = []
        ctx = _Ctx(known_vars, known_lists, {}, params=params,
                   pinfo=pinfo, current_proc=name)
        _emit_body(pd.get("c", []) or [], ctx, 1, body_lines)
        pset = set(params)
        glob = [v for v in known_vars if v in ctx.assigned and v not in pset]
        glob += [lst for lst in known_lists if lst in ctx.assigned and lst not in pset]
        if glob:
            out.append(_INDENT + "global " + ", ".join(glob))
        if not body_lines:
            out.append(_INDENT + "pass")
        else:
            out.extend(body_lines)

    # group scripts by lifecycle function, preserving order (so two on_draw hats
    # concatenate into one _draw body in the order they appear).
    order = ["_init", "_update", "_draw"]
    bodies = {"_init": [], "_update": [], "_draw": []}
    for s in scripts:
        d = CATALOG.get(s.get("t"))
        if d is None or d["shape"] != SHAPE_HAT:
            raise BlockError("top-level script is not an event: " + str(s.get("t")))
        bodies[d["lifecycle"]].append(s)

    for fn in order:
        hats = bodies[fn]
        if not hats:
            continue
        out.append("")
        out.append("")
        sig = "def " + fn + "(dt):" if fn == "_update" else "def " + fn + "():"
        out.append(sig)

        # emit the body to a scratch list first so we can compute `global` hoisting.
        body_lines = []
        ctx = _Ctx(known_vars, known_lists, {}, pinfo=pinfo)
        for hat in hats:
            _emit_body(hat.get("c", []) or [], ctx, 1, body_lines)

        # hoist globals: any declared var/list this function REASSIGNS must be `global`
        # (set/change a variable, clear a list, bind a for-each loop var). In-place list
        # mutation -- .append/_lset/_lremove -- touches the existing object, so it needs
        # none. Order: vars first then lists, matching the module-level inits.
        glob = [v for v in known_vars if v in ctx.assigned]
        glob += [lst for lst in known_lists if lst in ctx.assigned]
        if glob:
            out.append(_INDENT + "global " + ", ".join(glob))
        # if the whole function ended up empty (no hat had a body), `pass` it.
        if not body_lines:
            out.append(_INDENT + "pass")
        else:
            out.extend(body_lines)

    return "\n".join(out) + "\n"


# ============================================================================
# GRADUATION (spec Section 8, the MakeCode model): does a code commit still
# round-trip to the blocks, or has the kid written past the vocabulary?
# ============================================================================
#
# Blocks and code are two views of ONE program: the blocks GENERATE the code. A
# block-authored cart round-trips as long as its committed main.py still MATCHES
# the source its blocks.json regenerates. The moment a hand-edit diverges past
# what the blocks express, the kid has GRADUATED (a one-way door, spec Section 8):
# blocks go read-only and `manifest.graduated` is set.
#
# There is NO code -> blocks parser here, so "still within the vocabulary" is
# decided the only honest way we can: recompile the FROZEN blocks.json and compare
# its output to the committed source, up to a NORMALIZATION that erases exactly the
# differences a round trip (or a kid's cosmetic edit) is ALLOWED to introduce
# without changing the program. That normalization IS the heuristic, and it is
# tuned SHIP-CONSERVATIVE (plan Section 3 Stage 8): it eats cosmetics aggressively
# so a stray newline/comment can NEVER graduate a kid, and touches nothing
# structural so a genuine edit still does. A missed graduation is harmless (the
# next real divergence catches it); a false one locks a kid out of blocks, so the
# heuristic must EARN a graduation, never assume one.


def _strip_inline_comment(line):
    """Return `line` with any trailing `# ...` comment removed, respecting string
    literals (a `#` inside a quoted string is NOT a comment). MicroPython-safe: a
    plain left-to-right character scan, no `re`/`tokenize`. Handles ' and " strings
    with backslash escapes. The block compiler never emits triple-quoted strings, so
    a per-line scan is sufficient; a line with an unterminated quote just yields "no
    comment here", which is safe -- it can only KEEP more text, never cut the kid's
    code mid-string."""
    quote = None
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 2                   # skip the escaped char
                continue
            if ch == quote:
                quote = None
        elif ch == "#":
            return line[:i]
        elif ch == "'" or ch == '"':
            quote = ch
        i += 1
    return line


def normalize_for_roundtrip(src):
    """Normalize `src` for the graduation round-trip compare (spec Section 8).

    The compare asks: "does this source still match what the blocks regenerate?" So
    we erase ONLY the differences a block round trip -- or a kid's COSMETIC edit --
    is allowed to introduce without changing the program, and keep everything
    semantic. The exact, documented rules (the fuzzy heart of Stage 8):

      * each line's TRAILING whitespace is stripped
      * a trailing `#...` comment is stripped, string-literal-aware (so the
        BLOCK_MARKER line -- itself a comment -- and any comment a kid adds do NOT
        count: graduation is CONTENT-based, not marker-based, exactly as the plan
        requires)
      * a line that is now EMPTY (blank, or comment-only) is DROPPED entirely --
        spacing/comment lines never graduate
      * LEADING whitespace (indentation) is PRESERVED verbatim -- indentation is
        semantic in Python, so a re-indent IS a real change and MUST still graduate

    Bias (ship-conservative): aggressive on cosmetics, untouched on structure. What
    survives normalization is the program's actual statements, indent included; two
    sources normalize equal iff they are the same program modulo whitespace and
    comments."""
    out = []
    for raw in str(src).split("\n"):
        line = _strip_inline_comment(raw).rstrip()
        if line == "":
            continue                     # blank / comment-only line: cosmetic -> drop
        out.append(line)
    return "\n".join(out)


def source_roundtrips(program, src):
    """True if `src` still matches the source `program` (a blocks.json tree)
    regenerates, both normalized (normalize_for_roundtrip). This is the graduation
    oracle (spec Section 8): a block-authored cart whose code commit does NOT
    round-trip has diverged past the block vocabulary and should GRADUATE.

    Conservative on failure: a program that won't compile (a corrupt/partial tree
    raising BlockError) is treated as STILL round-tripping (returns True), so a
    transient compile problem can never graduate -- and thus lock out -- a kid by
    accident. When in doubt, do NOT graduate (plan Section 3 Stage 8)."""
    try:
        regen = compile_blocks(program)
    except Exception:  # noqa: BLE001 -- BlockError / bad tree: never graduate on it
        return True
    return normalize_for_roundtrip(regen) == normalize_for_roundtrip(src)


# ============================================================================
# Schema (de)serialization (the file IO lives in moy_carts.load/save_blocks)
# ============================================================================

def loads(text):
    """Parse blocks.json text into a program dict (raises on bad json)."""
    return json.loads(text)


def dumps(program):
    """Serialize a program dict to blocks.json text."""
    return json.dumps(program)

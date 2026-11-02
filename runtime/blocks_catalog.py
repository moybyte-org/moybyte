# Map (grep -n a name to jump there):
#   -- Slot types           a slot's type; the categories and shapes follow
#   -- THE VOCABULARY CATALOG  CATALOG: id -> category, shape, label, slots, emit
#   categories             the insert menu's category ids
#   block_def              the catalog entry for a block type id
#   slot_options           a dropdown slot's options
#   proc_name              a custom block's name (#48); proc_params, call_args beside it
#   block_slots            a block's slots, a custom block's from the live program
#   block_label            a block's label template
"""The block vocabulary of the #29 block programs and the queries over it: the
slot types, categories and shapes, `CATALOG` (every block type's category,
shape, label, slots and how it becomes code) and the custom blocks' labels and
slots derived from the live program.

The compiler (runtime/blocks.py) reads this table and re-exports every
public name, so `blocks.CATALOG` and the rest name these. A leaf with no
imports; MicroPython-safe like the compiler (no f-strings, plain dicts).
"""

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
CAT_ACTORS = "actors"        # #109: the SPRITE category (motion/sensing on self)
CAT_LOOKS = "looks"          # #85/#93: sprite appearance (show/hide/size/costume/say)
CAT_OPERATORS = "operators"
CAT_SOUND = "sound"
CAT_PROCS = "myblocks"       # #48: custom blocks (My Blocks / procedures)

CATEGORY_ORDER = [
    CAT_EVENTS, CAT_CONTROL, CAT_ACTORS, CAT_LOOKS, CAT_DRAW, CAT_INPUT,
    CAT_VARIABLES, CAT_LISTS, CAT_OPERATORS, CAT_SOUND, CAT_PROCS,
]

# A category's colour is the theme's `block_<category>` content role
# (native/moy_ui/style.json's theme section): the Scratch look by default --
# Motion=blue, Events=yellow, Control=orange, Looks=purple, Pen/Draw=dark
# green, Sensing/Input=indigo, Operators=green, Sound=pink, Data=red/peach,
# My Blocks=brown.


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
    # #85/#93: a per-sprite tap event (Scratch's "when this sprite clicked"). Its body
    # runs in _update guarded by a hit-test on the current actor -- see _HAT_GUARD. On
    # the Stage (no `self`) it's a safe no-op.
    "on_tap": {
        "category": CAT_EVENTS, "shape": SHAPE_HAT,
        "label": "when I'm tapped",
        "slots": [], "lifecycle": "_update",
    },
    # "when {button} pressed" -- body runs on the frame that button is first pressed
    # (btnp edge). Slot resolved into the guard (see _hat_guard_expr).
    "on_key": {
        "category": CAT_EVENTS, "shape": SHAPE_HAT,
        "label": "when {key} pressed",
        "slots": [_slot("key", SLOT_DROPDOWN, options="BUTTONS", default="a")],
        "lifecycle": "_update",
    },
    # broadcast / receive (Scratch's messages). broadcast is a plain statement; the
    # message is delivered to every "when I hear" hat on the NEXT frame (a deterministic
    # 1-frame, order-independent double-buffer -- see _pump_msgs).
    "broadcast": {
        "category": CAT_EVENTS, "shape": SHAPE_STATEMENT,
        "label": "broadcast {msg}",
        "slots": [_slot("msg", SLOT_TEXT, default="go")],
        "emit": "broadcast({msg})",
    },
    "on_receive": {
        "category": CAT_EVENTS, "shape": SHAPE_HAT,
        "label": "when I hear {msg}",
        "slots": [_slot("msg", SLOT_TEXT, default="go")],
        "lifecycle": "_update",
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
        "label": "touching {tag}?",
        # the current actor overlaps ANY live actor of tag {tag} (AABB, 8px boxes).
        "slots": [_slot("tag", SLOT_TEXT, default="")],
        "expr": "touching({__actor__}, {tag})",
    },
    "touching_edge": {
        "category": CAT_ACTORS, "shape": SHAPE_EXPR,
        "label": "touching edge?",
        "slots": [],
        "expr": "_atedge({__actor__})",
    },
    # -- self motion (#85/#93, Scratch's Motion category): read/set MY position -----
    "my_x": {
        "category": CAT_ACTORS, "shape": SHAPE_EXPR,
        "label": "my x", "slots": [], "expr": "_ax({__actor__})",
    },
    "my_y": {
        "category": CAT_ACTORS, "shape": SHAPE_EXPR,
        "label": "my y", "slots": [], "expr": "_ay({__actor__})",
    },
    "set_my_x": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "set my x to {x}",
        "slots": [_slot("x", SLOT_EXPR, default=0)],
        "emit": "_setax({__actor__}, {x})",
    },
    "set_my_y": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "set my y to {y}",
        "slots": [_slot("y", SLOT_EXPR, default=0)],
        "emit": "_setay({__actor__}, {y})",
    },
    "change_my_x": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "change my x by {dx}",
        "slots": [_slot("dx", SLOT_EXPR, default=0)],
        "emit": "_chax({__actor__}, {dx})",
    },
    "change_my_y": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "change my y by {dy}",
        "slots": [_slot("dy", SLOT_EXPR, default=0)],
        "emit": "_chay({__actor__}, {dy})",
    },
    # -- direction / steps (Scratch's default motion) --------------------------
    "point_dir": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "point in direction {d}",
        "slots": [_slot("d", SLOT_NUMBER, default=90)],
        "emit": "_setdir({__actor__}, {d})",
    },
    "turn_deg": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "turn {d} degrees",
        "slots": [_slot("d", SLOT_NUMBER, default=15)],
        "emit": "_turn({__actor__}, {d})",
    },
    "move_steps": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "move {n} steps",
        "slots": [_slot("n", SLOT_EXPR, default=10)],
        "emit": "_movesteps({__actor__}, {n})",
    },
    "my_dir": {
        "category": CAT_ACTORS, "shape": SHAPE_EXPR,
        "label": "my direction", "slots": [], "expr": "_getdir({__actor__})",
    },
    "bounce_edge": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "if on edge, bounce",
        "slots": [], "emit": "_bounce({__actor__})",
    },
    "set_rot_style": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "set rotation style {style}",
        "slots": [_slot("style", SLOT_DROPDOWN, options="ROTSTYLES",
                        default="all around")],
        "emit": "_setrot({__actor__}, {style})",
    },
    "move_actor_by": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "move by {dx} {dy}",
        "slots": [_slot("dx", SLOT_EXPR, default=0), _slot("dy", SLOT_EXPR, default=0)],
        "emit": "move_actor({__actor__}, {dx}, {dy})",
    },
    "move_actor_to": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "go to x {x} y {y}",
        "slots": [_slot("x", SLOT_EXPR, default=0), _slot("y", SLOT_EXPR, default=0)],
        "emit": "move_actor_to({__actor__}, {x}, {y})",
    },
    "remove_actor": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "remove me",
        "slots": [],
        "emit": "remove_actor({__actor__})",
    },
    "draw_scene": {
        "category": CAT_ACTORS, "shape": SHAPE_STATEMENT,
        "label": "draw scene",
        "slots": [],
        "emit": "draw_scene()",
    },

    # -- looks (#85/#93, Scratch's Looks): the current sprite's appearance -----
    "show": {
        "category": CAT_LOOKS, "shape": SHAPE_STATEMENT,
        "label": "show", "slots": [], "emit": "_show({__actor__}, True)",
    },
    "hide": {
        "category": CAT_LOOKS, "shape": SHAPE_STATEMENT,
        "label": "hide", "slots": [], "emit": "_show({__actor__}, False)",
    },
    "set_size": {
        "category": CAT_LOOKS, "shape": SHAPE_STATEMENT,
        "label": "set size to {n} %",
        "slots": [_slot("n", SLOT_NUMBER, default=100)],
        "emit": "_setsize({__actor__}, {n})",
    },
    "change_size": {
        "category": CAT_LOOKS, "shape": SHAPE_STATEMENT,
        "label": "change size by {n}",
        "slots": [_slot("n", SLOT_NUMBER, default=10)],
        "emit": "_chsize({__actor__}, {n})",
    },
    "switch_costume": {
        "category": CAT_LOOKS, "shape": SHAPE_STATEMENT,
        "label": "switch costume to {n}",
        "slots": [_slot("n", SLOT_NUMBER, default=0)],
        "emit": "_setcostume({__actor__}, {n})",
    },
    "next_costume": {
        "category": CAT_LOOKS, "shape": SHAPE_STATEMENT,
        "label": "next costume", "slots": [], "emit": "_nextcostume({__actor__})",
    },
    "say": {
        "category": CAT_LOOKS, "shape": SHAPE_STATEMENT,
        "label": "say {text}",
        "slots": [_slot("text", SLOT_TEXT, default="Hi!")],
        "emit": "_say({__actor__}, {text})",
    },
    "my_size": {
        "category": CAT_LOOKS, "shape": SHAPE_EXPR,
        "label": "my size", "slots": [], "expr": "_getsize({__actor__})",
    },
    "my_costume": {
        "category": CAT_LOOKS, "shape": SHAPE_EXPR,
        "label": "costume #", "slots": [], "expr": "_getcostume({__actor__})",
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
ROTSTYLES = ["all around", "left-right", "don't rotate"]   # #85/#93 Scratch rotation styles
OPTION_SETS = {"COLORS": COLORS, "BUTTONS": BUTTONS, "ROTSTYLES": ROTSTYLES}


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

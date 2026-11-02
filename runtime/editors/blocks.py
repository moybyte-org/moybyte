# Map (grep -n a name to jump there):
#   -- The vocabulary and its queries  blocks_catalog's CATALOG, categories, block_def, re-exported
#   make_block             build a block dict
#   empty_program          a new program with the three lifecycle hats
#   BlockError             a program names an unknown block type or slot
#   walk_tree              visit every block under the roots
#   collect_vars           the declared variables
#   collect_procs          the declared custom blocks
#   -- per-object scripts  a scene object's own scripts, run per live actor
#   sanitize_var_name      a kid's typed name made a safe Python identifier
#   parse_number_literal   a kid's typed number made a stored literal
#   compile_blocks         a block program -> cart source
#   source_roundtrips      does the source still match the program (graduation)
#   loads                  blocks.json text -> program
#   dumps                  program -> blocks.json text
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

# The cart API's names, once (see runtime/run/cart_verbs.py) -- _RESERVED_NAMES
# below is derived from them.
try:
    from cart_verbs import CART_VERBS as _CART_VERBS
except ImportError:  # pragma: no cover - host fallback
    from runtime.cart_verbs import CART_VERBS as _CART_VERBS


# The vocabulary and its queries (blocks_catalog), this module's names too.
try:
    from blocks_catalog import (SLOT_NUMBER, SLOT_TEXT, SLOT_VARIABLE, SLOT_LIST,
                                SLOT_DROPDOWN, SLOT_EXPR, CAT_EVENTS, CAT_CONTROL,
                                CAT_DRAW, CAT_INPUT, CAT_VARIABLES, CAT_LISTS,
                                CAT_ACTORS, CAT_LOOKS, CAT_OPERATORS, CAT_SOUND,
                                CAT_PROCS, CATEGORY_ORDER, SHAPE_HAT, SHAPE_STATEMENT,
                                SHAPE_CBLOCK, SHAPE_EXPR, SHAPE_DEF, CATALOG, PROC_DEF,
                                CALL, ELSE_MARKER, COLORS, BUTTONS, ROTSTYLES,
                                OPTION_SETS, categories, blocks_in_category, block_def,
                                slot_options, is_expr, is_cblock, is_def, proc_name,
                                proc_params, call_args, find_proc, block_slots,
                                block_label)
except ImportError:  # pragma: no cover - host fallback
    from runtime.blocks_catalog import (SLOT_NUMBER, SLOT_TEXT, SLOT_VARIABLE,
                                        SLOT_LIST, SLOT_DROPDOWN, SLOT_EXPR,
                                        CAT_EVENTS, CAT_CONTROL, CAT_DRAW, CAT_INPUT,
                                        CAT_VARIABLES, CAT_LISTS, CAT_ACTORS,
                                        CAT_LOOKS, CAT_OPERATORS, CAT_SOUND, CAT_PROCS,
                                        CATEGORY_ORDER, SHAPE_HAT, SHAPE_STATEMENT,
                                        SHAPE_CBLOCK, SHAPE_EXPR, SHAPE_DEF, CATALOG,
                                        PROC_DEF, CALL, ELSE_MARKER, COLORS, BUTTONS,
                                        ROTSTYLES, OPTION_SETS, categories,
                                        blocks_in_category, block_def, slot_options,
                                        is_expr, is_cblock, is_def, proc_name,
                                        proc_params, call_args, find_proc, block_slots,
                                        block_label)



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
# Sprite self-motion (#85/#93, Scratch's Motion category): read/set the CURRENT actor's
# position. All None-safe -- a stray "my x" outside any sprite reads 0 and a stray set
# no-ops, keeping the "a loose block never breaks the cart" discipline (like the actor
# verbs). `a` is the for_each / per-object loop's current actor (`_self`).
_HELPER_AX = (
    "def _ax(a):\n"
    "    return a.x if a else 0\n"
)
_HELPER_AY = (
    "def _ay(a):\n"
    "    return a.y if a else 0\n"
)
_HELPER_SETAX = (
    "def _setax(a, v):\n"
    "    if a is not None:\n"
    "        a.x = v\n"
)
_HELPER_SETAY = (
    "def _setay(a, v):\n"
    "    if a is not None:\n"
    "        a.y = v\n"
)
_HELPER_CHAX = (
    "def _chax(a, v):\n"
    "    if a is not None:\n"
    "        a.x = a.x + v\n"
)
_HELPER_CHAY = (
    "def _chay(a, v):\n"
    "    if a is not None:\n"
    "        a.y = a.y + v\n"
)
# Rotation style (Scratch): map the kid-facing dropdown to the flag draw_scene reads.
_HELPER_SETROT = (
    "def _setrot(a, s):\n"
    "    if a is not None:\n"
    "        a.flags['rot'] = ('leftright' if s == 'left-right'\n"
    "                          else 'none' if s == \"don't rotate\" else 'all')\n"
)
# "touching edge?" -- the current actor's 8px box past any screen edge (Scratch sensing).
_HELPER_ATEDGE = (
    "def _atedge(a):\n"
    "    if not a:\n"
    "        return False\n"
    "    return a.x < 0 or a.x > 312 or a.y < 0 or a.y > 232\n"
)
# "when I'm tapped" (on_tap): true on the frame the pointer taps INSIDE the actor's box.
_HELPER_TAPHIT = (
    "def _taphit(a):\n"
    "    t = touch()\n"
    "    if not a or not t or not t[2]:\n"
    "        return False\n"
    "    return a.x <= t[0] < a.x + 8 and a.y <= t[1] < a.y + 8\n"
)

# Direction / steps (Scratch's default motion). Direction lives in the per-actor
# `flags` dict (Scratch convention: 90 = right, 0 = up). None-safe. _movesteps uses
# `math`, imported into the header when the move-steps block is used.
_HELPER_MOTION = (
    "def _getdir(a):\n"
    "    return a.flags.get('dir', 90) if a else 90\n"
    "def _setdir(a, d):\n"
    "    if a is not None:\n"
    "        a.flags['dir'] = d % 360\n"
    "def _turn(a, d):\n"
    "    if a is not None:\n"
    "        a.flags['dir'] = (a.flags.get('dir', 90) + d) % 360\n"
    "def _movesteps(a, n):\n"
    "    if a is not None:\n"
    "        _r = math.radians(a.flags.get('dir', 90))\n"
    "        a.x = a.x + int(round(n * math.sin(_r)))\n"
    "        a.y = a.y - int(round(n * math.cos(_r)))\n"
    "def _bounce(a):\n"
    "    if a is None:\n"
    "        return\n"
    "    _d = a.flags.get('dir', 90)\n"
    "    if a.x <= 0 or a.x >= 312:\n"
    "        _d = (360 - _d) % 360\n"
    "        a.x = 1 if a.x <= 0 else 311\n"
    "    if a.y <= 0 or a.y >= 232:\n"
    "        _d = (180 - _d) % 360\n"
    "        a.y = 1 if a.y <= 0 else 231\n"
    "    a.flags['dir'] = _d\n"
)
# Looks (show/hide/size/costume/say): sprite appearance in the per-actor flags; the
# enhanced draw_scene (make_api) honours hidden / size / say when it paints the actors.
_HELPER_SHOW = (
    "def _show(a, v):\n"
    "    if a is not None:\n"
    "        a.flags['hidden'] = not v\n"
)
_HELPER_SIZE = (
    "def _setsize(a, n):\n"
    "    if a is not None:\n"
    "        a.flags['size'] = n\n"
    "def _chsize(a, n):\n"
    "    if a is not None:\n"
    "        a.flags['size'] = a.flags.get('size', 100) + n\n"
    "def _getsize(a):\n"
    "    return a.flags.get('size', 100) if a else 100\n"
)
_HELPER_COSTUME = (
    "def _setcostume(a, n):\n"
    "    if a is not None:\n"
    "        a.tile = int(n)\n"
    "def _nextcostume(a):\n"
    "    if a is not None:\n"
    "        a.tile = a.tile + 1\n"
    "def _getcostume(a):\n"
    "    return a.tile if a else 0\n"
)
_HELPER_SAY = (
    "def _say(a, s):\n"
    "    if a is not None:\n"
    "        a.flags['say'] = str(s)\n"
)
# Broadcast / receive (Scratch messages). A double-buffered mailbox: broadcast() drops
# into the NEXT set, _pump_msgs() (top of _update) swaps it into the LIVE set, and
# _received() reads the live set -- so delivery is a deterministic, order-independent
# 1-frame hop that every "when I hear" hat sees.
_HELPER_MSGS = (
    "_msg_now = set()\n"
    "_msg_next = set()\n"
    "def broadcast(m):\n"
    "    _msg_next.add(m)\n"
    "def _received(m):\n"
    "    return m in _msg_now\n"
    "def _pump_msgs():\n"
    "    global _msg_now, _msg_next\n"
    "    _msg_now = _msg_next\n"
    "    _msg_next = set()\n"
)


def _hat_guard_expr(hat, actor_var):
    """The `if <guard>:` expression for a CONDITIONAL event hat (Scratch's non-
    lifecycle events), or None for a plain lifecycle hat. {__actor__} resolves to the
    current sprite (or `None` on the Stage, where every guard is a safe no-op)."""
    t = hat.get("t")
    av = actor_var if actor_var else "None"
    if t == "on_tap":
        return "_taphit(" + av + ")"
    if t == "on_key":
        key = (hat.get("p", {}) or {}).get("key", "a")
        return "btnp(" + _render_text_literal(key) + ")"
    if t == "on_receive":
        msg = (hat.get("p", {}) or {}).get("msg", "")
        return "_received(" + _render_text_literal(msg) + ")"
    return None


def _uses_any(trees, needles):
    for n in needles:
        if _uses(trees, n):
            return True
    return False


def walk_tree(roots, visit):
    """Call `visit(node)` on every block dict under `roots`, pre-order.

    THE tree walk -- every schema container lives here and nowhere else, so a new
    one reaches the compiler's scans and the editor's renamers together. Recurses
    list-valued params (a call's positional args, #48), expression params ("p") and
    child bodies ("c"). p-before-c is arbitrary: visitors act on the node they are
    given, so the order is not observable (differentially checked, see
    tests/test_block_walk.py)."""
    def walk(node):
        if isinstance(node, list):
            for it in node:
                walk(it)
            return
        if not isinstance(node, dict):
            return
        visit(node)
        for v in (node.get("p", {}) or {}).values():
            walk(v)
        for c in node.get("c", []) or []:
            walk(c)

    for r in roots:
        walk(r)


def all_roots(program, procs=None, objects=None):
    """Every tree-walk root: global scripts + proc defs + every object's hats (#85/#93).

    `procs`/`objects` default to the raw program lists -- the live editor must never
    raise or drop mid-edit. The compiler passes its VALIDATED collections instead
    (collect_procs raises on a bad name, collect_objects drops untagged entries)."""
    roots = list(program.get("scripts", []) or [])
    roots += list(program.get("procs", []) or []) if procs is None else list(procs)
    for o in ((program.get("objects", []) or []) if objects is None else objects):
        roots += list(o.get("scripts", []) or [])
    return roots


def _uses(trees, needle):
    """True if any block tree in `trees` contains a block whose type id == needle."""
    found = [False]

    def visit(node):
        if node.get("t") == needle:
            found[0] = True

    walk_tree(trees, visit)
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
_RESERVED_NAMES = set(_CART_VERBS) | {
    # (The cart API verbs are DERIVED, from runtime/run/cart_verbs.CART_VERBS --
    # they used to be retyped here under a comment reading "keep in sync if
    # that grows", and by 2026-08-15 the copy was missing sspr/tline/tri/trib/
    # view/players/net/on_net. That is not cosmetic: a proc compiles to
    # `def <name>(...)`, so a kid who named a custom block `tri` shadowed the
    # verb and silently broke every triangle their cart drew.)
    "broadcast",                                          # #85/#93 messages
    # builtins the generated code relies on
    "int", "range", "len", "str", "min", "max", "abs", "round", "bool",
    # lifecycle functions + the compiler's own helpers (all `_`-prefixed)
    "_init", "_update", "_draw", "_touched", "_touch_x", "_touch_y", "_wait",
    "_wait_until", "_lget", "_lremove", "_lset", "_letter",
    "_ax", "_ay", "_setax", "_setay", "_chax", "_chay", "_atedge", "_taphit",
    "_getdir", "_setdir", "_turn", "_movesteps", "_bounce", "_setrot",
    "_show", "_setsize", "_chsize", "_getsize",
    "_setcostume", "_nextcostume", "_getcostume", "_say",
    "_msg_now", "_msg_next", "_received", "_pump_msgs",
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


# -- per-object scripts (#85/#93): a SCENE object's own scripts, run per live actor --
# Mirrors the #48 procs pattern: an OPTIONAL program["objects"] list, read via
# `.get(...) or []` so a pre-per-object program has no key -> [] -> the compiler emits
# nothing extra -> byte-identical output (and existing carts never spuriously graduate).
# Each entry is {"tag": <scene tag str>, "scripts": [on_start/on_update/on_draw hats]};
# compile_blocks wraps each non-empty hat body in `for _self in actors("<tag>"):`.

def collect_objects(program):
    """The validated per-object script list from program["objects"] (#85/#93). Each
    entry is {"tag": str, "scripts": [hat,...]}. Skips entries with a blank tag or no
    scripts and de-dups tags (first wins), so a tag compiles to exactly one actor
    loop per lifecycle. Never raises -- a malformed entry is dropped (the runtime-
    facing "a stray block never breaks the cart" discipline)."""
    raw = program.get("objects", []) or []
    out = []
    seen = {}
    for o in raw:
        if not isinstance(o, dict):
            continue
        tag = str(o.get("tag", "")).strip()
        if not tag or tag in seen:
            continue
        scripts = o.get("scripts", []) or []
        if not isinstance(scripts, list):
            continue
        seen[tag] = True
        out.append({"tag": tag, "scripts": scripts})
    return out


def object_has_content(obj):
    """True iff an object entry carries at least one non-empty hat body -- the test
    used to prune 'selected but never programmed' objects before save/compile."""
    for h in (obj.get("scripts", []) or []):
        if h.get("c"):
            return True
    return False


def prune_empty_objects(program):
    """Return a shallow copy of `program` with all-empty object entries dropped (a
    scene object the kid selected but never gave a block). Keeps blocks.json clean and
    stops an empty entry from forcing a draw_scene() into the output. Leaves the input
    untouched (the live editor keeps the entry so the kid can still add to it)."""
    objs = program.get("objects")
    if not objs:
        return program
    kept = [o for o in objs if object_has_content(o)]
    out = dict(program)
    if kept:
        out["objects"] = kept
    else:
        out.pop("objects", None)
    return out


def _calls_in(body, names):
    """The set of proc names this body (a statement/expr tree) directly calls."""
    out = set()

    def visit(node):
        if node.get("t") == CALL:
            tgt = proc_name(node)
            if tgt in names:
                out.add(tgt)

    walk_tree(body, visit)
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
    objects = collect_objects(program)             # #85/#93: per-object scripts (validated)
    scripts = program.get("scripts", []) or []
    # Helper detection + `global` hoisting must see proc and per-object bodies too
    # (a helper may be used only there), so scan every root together.
    use_trees = all_roots(program, procs, objects)

    out = [BLOCK_MARKER + " Edit in the block editor (or graduate to code)."]

    # `move N steps` needs trig; import it once at the top (portable-subset allowed).
    if _uses(use_trees, "move_steps"):
        out.append("")
        out.append("import math")

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
    # sprite self-motion / sensing helpers (#85/#93), each only when its block is used.
    if _uses(use_trees, "my_x"):
        out.append("")
        out.append(_HELPER_AX.rstrip("\n"))
    if _uses(use_trees, "my_y"):
        out.append("")
        out.append(_HELPER_AY.rstrip("\n"))
    if _uses(use_trees, "set_my_x"):
        out.append("")
        out.append(_HELPER_SETAX.rstrip("\n"))
    if _uses(use_trees, "set_my_y"):
        out.append("")
        out.append(_HELPER_SETAY.rstrip("\n"))
    if _uses(use_trees, "change_my_x"):
        out.append("")
        out.append(_HELPER_CHAX.rstrip("\n"))
    if _uses(use_trees, "change_my_y"):
        out.append("")
        out.append(_HELPER_CHAY.rstrip("\n"))
    if _uses(use_trees, "touching_edge"):
        out.append("")
        out.append(_HELPER_ATEDGE.rstrip("\n"))
    if _uses(use_trees, "on_tap"):
        out.append("")
        out.append(_HELPER_TAPHIT.rstrip("\n"))
    if _uses_any(use_trees, ("point_dir", "turn_deg", "move_steps",
                             "my_dir", "bounce_edge")):
        out.append("")
        out.append(_HELPER_MOTION.rstrip("\n"))
    if _uses(use_trees, "set_rot_style"):
        out.append("")
        out.append(_HELPER_SETROT.rstrip("\n"))
    if _uses_any(use_trees, ("show", "hide")):
        out.append("")
        out.append(_HELPER_SHOW.rstrip("\n"))
    if _uses_any(use_trees, ("set_size", "change_size", "my_size")):
        out.append("")
        out.append(_HELPER_SIZE.rstrip("\n"))
    if _uses_any(use_trees, ("switch_costume", "next_costume", "my_costume")):
        out.append("")
        out.append(_HELPER_COSTUME.rstrip("\n"))
    if _uses(use_trees, "say"):
        out.append("")
        out.append(_HELPER_SAY.rstrip("\n"))
    if _uses_any(use_trees, ("broadcast", "on_receive")):
        out.append("")
        out.append(_HELPER_MSGS.rstrip("\n"))

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

    # #85/#93: per-object script loops for this lifecycle -- one
    # `for _self in actors("<tag>"): <body>` per object that has a non-empty hat of
    # this kind. Precompute per fn so we know whether a function must be emitted even
    # when there are no GLOBAL hats (an object-only _update still needs a _update).
    obj_loops = {"_init": [], "_update": [], "_draw": []}
    for _o in objects:
        for _h in _o["scripts"]:
            _d = CATALOG.get(_h.get("t"))
            if (_d is not None and _d["shape"] == SHAPE_HAT
                    and (_h.get("c") or [])):        # skip empty hats -> no loop
                obj_loops[_d["lifecycle"]].append((_o["tag"], _h))
    # Actors are NOT auto-drawn (draw_scene paints them); when the program has any
    # per-object script, auto-emit a draw_scene() in _draw so the objects show up --
    # unless the kid already placed a `draw scene` block (never double-draw). Gated on
    # object loops existing, so an objects-less program's _draw is byte-identical.
    any_obj = any(obj_loops[fn] for fn in order)
    auto_draw_scene = any_obj and not _uses(use_trees, "draw_scene")

    for fn in order:
        hats = bodies[fn]
        oloops = obj_loops[fn]
        want_draw = (fn == "_draw" and auto_draw_scene)
        if not hats and not oloops and not want_draw:
            continue
        out.append("")
        out.append("")
        sig = "def " + fn + "(dt):" if fn == "_update" else "def " + fn + "():"
        out.append(sig)

        # emit the body to a scratch list first so we can compute `global` hoisting.
        body_lines = []
        ctx = _Ctx(known_vars, known_lists, {}, pinfo=pinfo)
        # Message delivery (broadcast/receive): swap the mailbox ONCE per frame, before
        # any hat runs, so every "when I hear" hat this frame sees the same delivery.
        if fn == "_update" and _uses_any(use_trees, ("broadcast", "on_receive")):
            body_lines.append(_INDENT + "_pump_msgs()")
        for hat in hats:
            # Skip an EMPTY global hat: _emit_body would pad it with a `pass` that,
            # once object loops / draw_scene follow, becomes dead code. A program with
            # exactly one (possibly empty) hat per kind -- i.e. every existing cart --
            # is byte-identical either way (an all-empty function still gets its single
            # `pass` from the trailing guard below).
            kids = hat.get("c") or []
            if not kids:
                continue
            g = _hat_guard_expr(hat, ctx.actor_var)  # conditional event -> wrap it
            if g:
                body_lines.append(_INDENT + "if " + g + ":")
                _emit_body(kids, ctx, 2, body_lines)
            else:
                _emit_body(kids, ctx, 1, body_lines)
        # draw the placed actors (after the global _draw body, so it paints over the
        # background; under any per-object _draw extras below).
        if want_draw:
            body_lines.append(_INDENT + "draw_scene()")
        # per-object scripts: `for _self in actors("<tag>"): <body>` with `self` bound
        # as the current actor (reuses the #109 actor verbs -- move/touching/remove
        # act on `self`). Vars the body reassigns land in this fn's `global` (shared
        # ctx); `_self` is synthetic (like `_actorN`), never hoisted.
        for tag, hat in oloops:
            body_lines.append(_INDENT + "for _self in actors("
                              + _render_text_literal(tag) + "):")
            prev = ctx.actor_var
            ctx.actor_var = "_self"
            kids = hat.get("c", []) or []
            g = _hat_guard_expr(hat, ctx.actor_var)  # ctx.actor_var == "_self" here
            if g:
                body_lines.append(_INDENT * 2 + "if " + g + ":")
                _emit_loop_body(kids, ctx, 3, body_lines)
            else:
                _emit_loop_body(kids, ctx, 2, body_lines)
            ctx.actor_var = prev

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

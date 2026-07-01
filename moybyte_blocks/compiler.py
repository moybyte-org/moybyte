"""Compile Moybyte block JSON into readable Python."""

import json
import os
import string

from .schema import validate_blocks


def _ident(name):
    if not isinstance(name, str) or not name:
        raise ValueError("identifier must be a non-empty string")
    out = []
    for index, ch in enumerate(name):
        ok = ch == "_" or ch.isalnum()
        if index == 0 and ch.isdigit():
            ok = False
        if not ok:
            raise ValueError("invalid identifier: " + name)
        out.append(ch)
    return "".join(out)


def _quote(value):
    return json.dumps(value)


def _template_expr(value):
    formatter = string.Formatter()
    has_fields = False
    for _literal, field_name, _format_spec, _conversion in formatter.parse(value):
        if field_name is not None:
            has_fields = True
            break
    quoted = _quote(value)
    if has_fields:
        return "f" + quoted
    return quoted


def _indent(level):
    return "    " * level


def _emit_statement(block, lines, level, globals_needed):
    kind = block.get("type")
    p = _indent(level)
    if kind == "clear":
        color = block.get("color", 0)
        lines.append(p + "clear(" + repr(color) + ")")
    elif kind == "text":
        value = block.get("value", "")
        lines.append(
            p
            + "text("
            + _template_expr(value)
            + ", "
            + repr(block.get("x", 0))
            + ", "
            + repr(block.get("y", 0))
            + ")"
        )
    elif kind == "draw_sprite":
        lines.append(p + "draw_sprite(" + _ident(block["sprite"]) + ")")
    elif kind == "if_button":
        lines.append(p + "if button(" + _quote(block["button"]) + "):")
        body = block.get("body", [])
        if body:
            for child in body:
                _emit_statement(child, lines, level + 1, globals_needed)
        else:
            lines.append(_indent(level + 1) + "pass")
    elif kind == "move_sprite":
        name = _ident(block["sprite"])
        dx = block.get("dx", 0)
        dy = block.get("dy", 0)
        if dx:
            lines.append(p + name + ".x += " + repr(dx))
        if dy:
            lines.append(p + name + ".y += " + repr(dy))
        if not dx and not dy:
            lines.append(p + "pass")
    elif kind == "set_sprite_pos":
        name = _ident(block["sprite"])
        lines.append(p + name + ".move_to(" + repr(block.get("x", 0)) + ", " + repr(block.get("y", 0)) + ")")
    elif kind == "if_touching":
        lines.append(p + "if " + _ident(block["a"]) + ".touching(" + _ident(block["b"]) + "):")
        body = block.get("body", [])
        if body:
            for child in body:
                _emit_statement(child, lines, level + 1, globals_needed)
        else:
            lines.append(_indent(level + 1) + "pass")
    elif kind == "change_var":
        name = _ident(block["name"])
        globals_needed.add(name)
        lines.append(p + name + " += " + repr(block.get("delta", 1)))
    elif kind == "set_var":
        name = _ident(block["name"])
        globals_needed.add(name)
        lines.append(p + name + " = " + repr(block.get("value", 0)))
    elif kind == "beep":
        lines.append(p + "beep()")
    elif kind == "wait":
        lines.append(p + "# wait is ignored by the v0 frame runtime")
    elif kind == "send_radio":
        lines.append(p + "radio.send(" + _quote(block.get("message", "")) + ")")
    else:
        raise ValueError("unknown block type: " + str(kind))


def compile_blocks(data):
    data = validate_blocks(data)
    lines = [
        "# Generated from Moybyte Blocks. Edits may be overwritten.",
        "from moybyte import *",
        "",
    ]

    for var in data.get("variables", []):
        lines.append(_ident(var["name"]) + " = " + _quote(var.get("initial", 0)))
    if data.get("variables"):
        lines.append("")

    for item in data.get("sprites", []):
        name = _ident(item["name"])
        asset = item.get("asset", item["name"])
        x = item.get("x", 0)
        y = item.get("y", 0)
        w = item.get("w", 8)
        h = item.get("h", 8)
        lines.append(
            name
            + " = sprite("
            + _quote(asset)
            + ", x="
            + repr(x)
            + ", y="
            + repr(y)
            + ", w="
            + repr(w)
            + ", h="
            + repr(h)
            + ")"
        )
    if data.get("sprites"):
        lines.append("")

    scripts = data.get("scripts", [])
    emitted = {"update": False, "draw": False}
    for script in scripts:
        event = script.get("event", {}).get("type")
        if event not in ["update", "draw"]:
            raise ValueError("unsupported script event: " + str(event))
        emitted[event] = True
        fn_args = "dt" if event == "update" else ""
        body_lines = []
        globals_needed = set()
        for block in script.get("body", []):
            _emit_statement(block, body_lines, 1, globals_needed)
        lines.append("# " + event.capitalize() + " script")
        lines.append("def " + event + "(" + fn_args + "):")
        if globals_needed:
            lines.append(_indent(1) + "global " + ", ".join(sorted(globals_needed)))
        if body_lines:
            lines.extend(body_lines)
        else:
            lines.append(_indent(1) + "pass")
        lines.append("")

    if not emitted["update"]:
        lines.extend(["def update(dt):", _indent(1) + "pass", ""])
    if not emitted["draw"]:
        lines.extend(["def draw():", _indent(1) + "pass", ""])

    lines.append("run(update=update, draw=draw)")
    lines.append("")
    return "\n".join(lines)


def compile_project(project_path, out_path=None):
    blocks_path = os.path.join(project_path, "blocks.json")
    with open(blocks_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    code = compile_blocks(data)
    if out_path is None:
        out_dir = os.path.join(project_path, "generated")
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)
        out_path = os.path.join(out_dir, "main.generated.py")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(code)
    return out_path

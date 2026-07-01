"""Validation for blocks.json."""

import string

from moybyte.errors import ManifestError
from moybyte.input import BUTTONS

BLOCK_SCHEMA = "moybyte.blocks.v1"
EVENT_TYPES = ["update", "draw"]
BLOCK_TYPES = [
    "beep",
    "change_var",
    "clear",
    "draw_sprite",
    "if_button",
    "if_touching",
    "move_sprite",
    "send_radio",
    "set_sprite_pos",
    "set_var",
    "text",
    "wait",
]


class BlockValidationError(ManifestError):
    """Raised when blocks.json is structurally invalid."""


def _fail(path, message):
    raise BlockValidationError(path + ": " + message)


def _is_identifier(value):
    if not isinstance(value, str) or not value:
        return False
    if value[0].isdigit():
        return False
    for ch in value:
        if ch != "_" and not ch.isalnum():
            return False
    return True


def _require_object(value, path):
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    return value


def _require_list(value, path):
    if not isinstance(value, list):
        _fail(path, "must be a list")
    return value


def _require_name(value, path):
    if not _is_identifier(value):
        _fail(path, "must be a valid identifier")
    return value


def _require_number(value, path):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        _fail(path, "must be a number")
    return value


def _require_scalar(value, path):
    if not isinstance(value, (str, int, float, bool)) and value is not None:
        _fail(path, "must be a string, number, boolean, or null")
    return value


def _validate_template(value, path, variables):
    if not isinstance(value, str):
        _fail(path, "must be a string")
    formatter = string.Formatter()
    try:
        parts = list(formatter.parse(value))
    except ValueError as exc:
        _fail(path, "has invalid template braces: " + str(exc))
    for _literal, field_name, _format_spec, conversion in parts:
        if field_name is None:
            continue
        if conversion is not None:
            _fail(path, "must not use template conversions")
        if "." in field_name or "[" in field_name or not _is_identifier(field_name):
            _fail(path, "template field '" + field_name + "' must be a simple variable name")
        if field_name not in variables:
            _fail(path, "template field '" + field_name + "' does not match a variable")


def _validate_body(body, path, sprites, variables):
    _require_list(body, path)
    for index, block in enumerate(body):
        _validate_block(block, path + "[" + str(index) + "]", sprites, variables)


def _validate_block(block, path, sprites, variables):
    _require_object(block, path)
    block_type = block.get("type")
    if block_type not in BLOCK_TYPES:
        _fail(path + ".type", "must be one of: " + ", ".join(BLOCK_TYPES))

    if block_type == "clear":
        if "color" in block:
            _require_number(block["color"], path + ".color")
    elif block_type == "text":
        if "value" not in block:
            _fail(path + ".value", "is required")
        _validate_template(block["value"], path + ".value", variables)
        _require_number(block.get("x", 0), path + ".x")
        _require_number(block.get("y", 0), path + ".y")
    elif block_type == "draw_sprite":
        sprite = block.get("sprite")
        _require_name(sprite, path + ".sprite")
        if sprite not in sprites:
            _fail(path + ".sprite", "references unknown sprite '" + sprite + "'")
    elif block_type == "if_button":
        button = block.get("button")
        if button not in BUTTONS:
            _fail(path + ".button", "must be a known button name")
        _validate_body(block.get("body", []), path + ".body", sprites, variables)
    elif block_type == "move_sprite":
        sprite = block.get("sprite")
        _require_name(sprite, path + ".sprite")
        if sprite not in sprites:
            _fail(path + ".sprite", "references unknown sprite '" + sprite + "'")
        _require_number(block.get("dx", 0), path + ".dx")
        _require_number(block.get("dy", 0), path + ".dy")
    elif block_type == "set_sprite_pos":
        sprite = block.get("sprite")
        _require_name(sprite, path + ".sprite")
        if sprite not in sprites:
            _fail(path + ".sprite", "references unknown sprite '" + sprite + "'")
        _require_number(block.get("x", 0), path + ".x")
        _require_number(block.get("y", 0), path + ".y")
    elif block_type == "if_touching":
        for name in ["a", "b"]:
            sprite = block.get(name)
            _require_name(sprite, path + "." + name)
            if sprite not in sprites:
                _fail(path + "." + name, "references unknown sprite '" + sprite + "'")
        _validate_body(block.get("body", []), path + ".body", sprites, variables)
    elif block_type == "change_var":
        name = block.get("name")
        _require_name(name, path + ".name")
        if name not in variables:
            _fail(path + ".name", "references unknown variable '" + name + "'")
        _require_number(block.get("delta", 1), path + ".delta")
    elif block_type == "set_var":
        name = block.get("name")
        _require_name(name, path + ".name")
        if name not in variables:
            _fail(path + ".name", "references unknown variable '" + name + "'")
        _require_scalar(block.get("value", 0), path + ".value")
    elif block_type == "wait":
        if "seconds" in block:
            seconds = _require_number(block["seconds"], path + ".seconds")
            if seconds < 0:
                _fail(path + ".seconds", "must be zero or greater")
    elif block_type == "send_radio":
        _require_scalar(block.get("message", ""), path + ".message")


def validate_blocks(data):
    if not isinstance(data, dict):
        raise BlockValidationError("blocks file must be an object")
    if data.get("schema") != BLOCK_SCHEMA:
        raise BlockValidationError("blocks.schema must be " + BLOCK_SCHEMA)
    for name in ["variables", "sprites", "scripts"]:
        if name in data and not isinstance(data[name], list):
            raise BlockValidationError("blocks." + name + " must be a list")

    variables = set()
    for index, var in enumerate(data.get("variables", [])):
        path = "blocks.variables[" + str(index) + "]"
        _require_object(var, path)
        name = _require_name(var.get("name"), path + ".name")
        if name in variables:
            _fail(path + ".name", "duplicates variable '" + name + "'")
        variables.add(name)
        if "initial" in var:
            _require_scalar(var["initial"], path + ".initial")

    sprites = set()
    for index, sprite in enumerate(data.get("sprites", [])):
        path = "blocks.sprites[" + str(index) + "]"
        _require_object(sprite, path)
        name = _require_name(sprite.get("name"), path + ".name")
        if name in sprites:
            _fail(path + ".name", "duplicates sprite '" + name + "'")
        sprites.add(name)
        if "asset" in sprite and not isinstance(sprite["asset"], str):
            _fail(path + ".asset", "must be a string")
        for field in ["x", "y"]:
            if field in sprite:
                _require_number(sprite[field], path + "." + field)
        for field in ["w", "h"]:
            value = _require_number(sprite.get(field, 8), path + "." + field)
            if value <= 0:
                _fail(path + "." + field, "must be greater than zero")

    for index, script in enumerate(data.get("scripts", [])):
        path = "blocks.scripts[" + str(index) + "]"
        _require_object(script, path)
        event = _require_object(script.get("event"), path + ".event")
        event_type = event.get("type")
        if event_type not in EVENT_TYPES:
            _fail(path + ".event.type", "must be one of: " + ", ".join(EVENT_TYPES))
        _validate_body(script.get("body", []), path + ".body", sprites, variables)

    return data

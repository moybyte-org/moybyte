"""Project manifest loading and validation."""

import json
import os

from .errors import ManifestError
from .permissions import Permissions

SCHEMA = "kidcode.project.v1"
KINDS = ["game", "app", "demo", "tool"]
AGE_MODES = ["cards", "blocks", "text", "advanced"]


def _require_string(data, name):
    value = data.get(name)
    if not isinstance(value, str) or not value:
        raise ManifestError("manifest." + name + " must be a non-empty string")
    return value


def _validate_slug(value):
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789_"
    for ch in value:
        if ch not in allowed:
            raise ManifestError("manifest.id must use lowercase letters, numbers, and underscores")


def resolve_project_file(project_path, relative_path, label="file"):
    if not isinstance(relative_path, str) or not relative_path:
        raise ManifestError(label + " must be a non-empty project-relative path")
    if os.path.isabs(relative_path):
        raise ManifestError(label + " must be relative to the project folder")
    root = os.path.abspath(project_path)
    full = os.path.abspath(os.path.join(root, relative_path))
    if full != root and not full.startswith(root + os.sep):
        raise ManifestError(label + " escapes the project folder: " + relative_path)
    if not os.path.exists(full):
        raise ManifestError(label + " file does not exist: " + relative_path)
    return full


class Canvas:
    def __init__(self, width=128, height=128, scale=4):
        self.width = int(width)
        self.height = int(height)
        self.scale = int(scale)
        if self.width <= 0 or self.height <= 0 or self.scale <= 0:
            raise ManifestError("canvas width, height, and scale must be positive")

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ManifestError("canvas must be an object")
        return cls(
            width=data.get("width", 128),
            height=data.get("height", 128),
            scale=data.get("scale", 4),
        )

    def to_dict(self):
        return {"width": self.width, "height": self.height, "scale": self.scale}


class Manifest:
    def __init__(
        self,
        project_id,
        title,
        kind,
        age_mode,
        entry,
        canvas,
        permissions,
        schema=SCHEMA,
        raw=None,
    ):
        if schema != SCHEMA:
            raise ManifestError("manifest.schema must be " + SCHEMA)
        if kind not in KINDS:
            raise ManifestError("manifest.kind must be one of: " + ", ".join(KINDS))
        if age_mode not in AGE_MODES:
            raise ManifestError("manifest.age_mode must be one of: " + ", ".join(AGE_MODES))
        _validate_slug(project_id)
        self.schema = schema
        self.id = project_id
        self.title = title
        self.kind = kind
        self.age_mode = age_mode
        self.entry = entry
        self.canvas = canvas
        self.permissions = permissions
        self.raw = raw or {}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ManifestError("manifest must be an object")
        schema = _require_string(data, "schema")
        project_id = _require_string(data, "id")
        title = _require_string(data, "title")
        kind = _require_string(data, "kind")
        age_mode = _require_string(data, "age_mode")
        entry = _require_string(data, "entry")
        canvas = Canvas.from_dict(data.get("canvas"))
        permissions = Permissions.from_dict(data.get("permissions"))
        return cls(project_id, title, kind, age_mode, entry, canvas, permissions, schema, data)

    @classmethod
    def load(cls, project_path):
        manifest_path = os.path.join(project_path, "manifest.json")
        if not os.path.exists(manifest_path):
            raise ManifestError("missing manifest.json in " + project_path)
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        manifest = cls.from_dict(data)
        resolve_project_file(project_path, manifest.entry, "entry")
        return manifest

    def to_dict(self):
        data = dict(self.raw)
        data["schema"] = self.schema
        data["id"] = self.id
        data["title"] = self.title
        data["kind"] = self.kind
        data["age_mode"] = self.age_mode
        data["entry"] = self.entry
        data["canvas"] = self.canvas.to_dict()
        data["permissions"] = self.permissions.to_dict()
        return data

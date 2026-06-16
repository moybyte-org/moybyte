"""Project scaffolding helpers for the KidCode CLI."""

import json
import os

from kidcode.errors import ManifestError


def slugify(value):
    value = value.strip().lower().replace("-", "_").replace(" ", "_")
    out = []
    last_underscore = False
    for ch in value:
        if ch.isalnum():
            out.append(ch)
            last_underscore = False
        elif ch == "_" and not last_underscore:
            out.append(ch)
            last_underscore = True
    slug = "".join(out).strip("_")
    if not slug:
        raise ManifestError("project name must contain at least one letter or number")
    if slug[0].isdigit():
        slug = "project_" + slug
    return slug


def title_from_slug(slug):
    return " ".join(part.capitalize() for part in slug.split("_") if part)


def project_dir_for(path):
    if path.endswith(".kcproj"):
        return path
    return path + ".kcproj"


def starter_code(kind):
    if kind == "app":
        return (
            "from kidcode import *\n"
            "\n"
            "\n"
            "@game.draw\n"
            "def draw():\n"
            "    clear()\n"
            "    text(\"Hello KidCode\", 4, 4)\n"
            "\n"
            "\n"
            "run()\n"
        )
    return (
        "from kidcode import *\n"
        "\n"
        "player = sprite(\"player\", x=60, y=60, w=8, h=8)\n"
        "\n"
        "\n"
        "@game.update\n"
        "def update(dt):\n"
        "    if button(\"left\"):\n"
        "        player.x -= 2\n"
        "    if button(\"right\"):\n"
        "        player.x += 2\n"
        "    if button(\"up\"):\n"
        "        player.y -= 2\n"
        "    if button(\"down\"):\n"
        "        player.y += 2\n"
        "\n"
        "\n"
        "@game.draw\n"
        "def draw():\n"
        "    clear()\n"
        "    draw_sprite(player)\n"
        "\n"
        "\n"
        "run()\n"
    )


def create_project(path, project_id=None, title=None, kind="game", age_mode="text"):
    project_dir = project_dir_for(path)
    base = os.path.basename(project_dir)
    if base.endswith(".kcproj"):
        base = base[:-7]
    project_id = project_id or slugify(base)
    title = title or title_from_slug(project_id)

    if os.path.exists(project_dir):
        raise ManifestError("project already exists: " + project_dir)

    os.makedirs(project_dir)
    manifest = {
        "schema": "kidcode.project.v1",
        "id": project_id,
        "title": title,
        "kind": kind,
        "age_mode": age_mode,
        "entry": "main.py",
        "canvas": {"width": 128, "height": 128, "scale": 4},
        "permissions": {
            "files": "project",
            "sd_card": False,
            "audio": True,
            "radio": False,
            "wifi": False,
            "ai": False,
            "gpio": False,
            "system": False,
        },
    }
    with open(os.path.join(project_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    with open(os.path.join(project_dir, "main.py"), "w", encoding="utf-8") as fh:
        fh.write(starter_code(kind))
    return project_dir

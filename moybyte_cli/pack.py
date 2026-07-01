"""Project bundle creation."""

import json
import os
import zipfile

from moybyte.errors import ManifestError
from moybyte.manifest import Manifest

BUNDLE_SCHEMA = "moybyte.bundle.v1"
DEFAULT_EXCLUDED_DIRS = [".git", ".pytest_cache", "__pycache__", "generated"]
DEFAULT_EXCLUDED_SUFFIXES = [".pyc", ".pyo"]


def default_bundle_path(project_path):
    root = project_path[:-8] if project_path.endswith(".moyproj") else project_path
    return root + ".kc8"


def _should_skip(rel_path, include_generated):
    parts = rel_path.split(os.sep)
    excluded_dirs = list(DEFAULT_EXCLUDED_DIRS)
    if include_generated:
        excluded_dirs.remove("generated")
    for part in parts:
        if part in excluded_dirs:
            return True
    for suffix in DEFAULT_EXCLUDED_SUFFIXES:
        if rel_path.endswith(suffix):
            return True
    return False


def project_files(project_path, include_generated=False):
    root = os.path.abspath(project_path)
    files = []
    for current, dirs, names in os.walk(root):
        rel_dir = os.path.relpath(current, root)
        if rel_dir == ".":
            rel_dir = ""
        dirs[:] = [
            name
            for name in dirs
            if not _should_skip(os.path.join(rel_dir, name), include_generated)
        ]
        for name in names:
            full = os.path.join(current, name)
            rel = os.path.relpath(full, root)
            if _should_skip(rel, include_generated):
                continue
            files.append(rel)
    return sorted(files)


def pack_project(project_path, out_path=None, include_generated=False):
    project_path = os.path.abspath(project_path)
    if not os.path.isdir(project_path):
        raise ManifestError("project must be a folder: " + project_path)
    manifest = Manifest.load(project_path)
    out_path = os.path.abspath(out_path or default_bundle_path(project_path))
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    files = project_files(project_path, include_generated=include_generated)
    metadata = {
        "schema": BUNDLE_SCHEMA,
        "project_id": manifest.id,
        "title": manifest.title,
        "files": files,
    }

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("moybyte_bundle.json", json.dumps(metadata, indent=2) + "\n")
        for rel in files:
            archive.write(os.path.join(project_path, rel), rel)
    return out_path

import pytest

from moybyte.errors import ManifestError
from moybyte.manifest import Manifest, resolve_project_file


def test_manifest_loads_tiny_runner():
    manifest = Manifest.load("examples/tiny_runner.moyproj")

    assert manifest.id == "tiny_runner"
    assert manifest.title == "Tiny Runner"
    assert manifest.canvas.width == 128
    assert manifest.permissions.audio is True


def test_manifest_rejects_bad_schema():
    with pytest.raises(ManifestError):
        Manifest.from_dict(
            {
                "schema": "wrong",
                "id": "bad",
                "title": "Bad",
                "kind": "game",
                "age_mode": "text",
                "entry": "main.py",
                "canvas": {"width": 128, "height": 128, "scale": 4},
                "permissions": {"files": "project"},
            }
        )


def test_project_file_must_not_escape_project(tmp_path):
    project = tmp_path / "bad.moyproj"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("", encoding="utf-8")

    with pytest.raises(ManifestError):
        resolve_project_file(str(project), "../outside.py", "entry")

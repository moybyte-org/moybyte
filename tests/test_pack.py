import json
import zipfile

from moybyte_cli.main import main
from moybyte_cli.pack import pack_project
from moybyte_cli.projects import create_project


def test_pack_project_creates_kc8_bundle(tmp_path):
    project = create_project(str(tmp_path / "bundle_game"))
    out = tmp_path / "bundle_game.kc8"

    result = pack_project(project, out_path=str(out))

    assert result == str(out)
    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
        assert "moybyte_bundle.json" in names
        assert "manifest.json" in names
        assert "main.py" in names
        metadata = json.loads(archive.read("moybyte_bundle.json").decode("utf-8"))
        assert metadata["schema"] == "moybyte.bundle.v1"
        assert metadata["project_id"] == "bundle_game"


def test_pack_project_excludes_generated_by_default(tmp_path):
    project = create_project(str(tmp_path / "generated_game"))
    generated = tmp_path / "generated_game.moyproj" / "generated"
    generated.mkdir()
    (generated / "main.generated.py").write_text("print('skip')\n", encoding="utf-8")
    out = tmp_path / "generated_game.kc8"

    pack_project(project, out_path=str(out))

    with zipfile.ZipFile(out) as archive:
        assert "generated/main.generated.py" not in archive.namelist()


def test_cli_pack(capsys, tmp_path):
    project = create_project(str(tmp_path / "cli_pack"))
    out = tmp_path / "cli_pack.kc8"

    result = main(["pack", project, "--out", str(out)])
    captured = capsys.readouterr()

    assert result == 0
    assert "packed:" in captured.out
    assert out.exists()

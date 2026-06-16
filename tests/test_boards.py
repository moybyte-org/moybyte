import json
import zipfile

from kidcode_cli.boards import board_profile_json, device_doctor, export_device_project
from kidcode_cli.main import main
from kidcode_cli.projects import create_project


def test_board_info_contains_lilygo_profile():
    profile = json.loads(board_profile_json("lilygo_t_deck_plus"))

    assert profile["mcu"] == "esp32s3"
    assert profile["platformio_env"] == "T-Deck"
    assert profile["pins"]["keyboard_int"] == 46


def test_device_doctor_reports_without_board_attached():
    info = device_doctor("lilygo_t_deck_plus")

    assert info["board"] == "lilygo_t_deck_plus"
    assert "serial_ports" in info


def test_export_device_project_creates_bundle_and_deploy_metadata(tmp_path):
    project = create_project(str(tmp_path / "deck_game"))
    out_dir = tmp_path / "export"

    result = export_device_project(project, "lilygo_t_deck_plus", str(out_dir))

    assert result == str(out_dir)
    deploy = json.loads((out_dir / "deploy.json").read_text(encoding="utf-8"))
    assert deploy["board"] == "lilygo_t_deck_plus"
    assert deploy["bundle"] == "deck_game.kc8"
    with zipfile.ZipFile(out_dir / "deck_game.kc8") as archive:
        assert "manifest.json" in archive.namelist()


def test_cli_device_commands(capsys, tmp_path):
    assert main(["board-info", "lilygo_t_deck_plus"]) == 0
    board_info = capsys.readouterr()
    assert "lilygo_t_deck_plus" in board_info.out

    assert main(["device-doctor", "--board", "lilygo_t_deck_plus"]) == 0
    doctor = capsys.readouterr()
    assert "KidCode device doctor" in doctor.out

    project = create_project(str(tmp_path / "cli_deck"))
    out_dir = tmp_path / "cli_export"
    assert main(["export-device", project, "--board", "lilygo_t_deck_plus", "--out", str(out_dir)]) == 0
    export = capsys.readouterr()
    assert "exported:" in export.out
    assert (out_dir / "deploy.json").exists()

import json
import zipfile

from moybyte_cli.boards import board_profile_json, device_doctor, export_device_project, smoke_check_log
from moybyte_cli.firmware import write_bundle_header
from moybyte_cli.main import main
from moybyte_cli.projects import create_project


def test_board_info_contains_lilygo_profile():
    profile = json.loads(board_profile_json("lilygo_t_deck_plus"))

    assert profile["mcu"] == "esp32s3"
    assert profile["platformio_env"] == "T-Deck"
    assert profile["pins"]["keyboard_addr"] == 0x55
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
    assert "Moybyte device doctor" in doctor.out

    project = create_project(str(tmp_path / "cli_deck"))
    out_dir = tmp_path / "cli_export"
    assert main(["export-device", project, "--board", "lilygo_t_deck_plus", "--out", str(out_dir)]) == 0
    export = capsys.readouterr()
    assert "exported:" in export.out
    assert (out_dir / "deploy.json").exists()


def test_write_bundle_header_creates_c_array(tmp_path):
    project = create_project(str(tmp_path / "header_game"))
    out = tmp_path / "moybyte_project_bundle.h"

    result = write_bundle_header(project, "lilygo_t_deck_plus", str(out))
    text = out.read_text(encoding="utf-8")

    assert result == str(out)
    assert '#define MOYBYTE_PROJECT_ID "header_game"' in text
    assert "#define MOYBYTE_PROJECT_BUNDLE_SIZE " in text
    assert "static const uint8_t MOYBYTE_PROJECT_BUNDLE[]" in text


def test_smoke_check_log_accepts_expected_serial_output(tmp_path):
    log = tmp_path / "serial.log"
    log.write_text(
        "Moybyte firmware smoke test\n"
        "Board id: lilygo_t_deck_plus\n"
        "Bundled project: tiny_runner\n"
        "Bundle bytes: 123\n"
        "Keyboard: detected\n"
        "Display: Moybyte native tiny_runner canvas\n"
        "Runtime: native tiny_runner scaffold\n"
        "Moybyte heartbeat 0\n"
        "Native tiny_runner player_x 62\n"
        "Native tiny_runner player_y 60\n"
        "Native buttons left/right/up/down 0/0/0/0\n",
        encoding="utf-8",
    )

    assert smoke_check_log(str(log), project_id="tiny_runner") == []


def test_smoke_check_log_rejects_missing_project(tmp_path):
    log = tmp_path / "serial.log"
    log.write_text(
        "Moybyte firmware smoke test\n"
        "Board id: lilygo_t_deck_plus\n"
        "Bundle bytes: 123\n"
        "Keyboard: detected\n"
        "Display: Moybyte native tiny_runner canvas\n"
        "Runtime: native tiny_runner scaffold\n"
        "Moybyte heartbeat 0\n"
        "Native tiny_runner player_x 62\n"
        "Native tiny_runner player_y 60\n"
        "Native buttons left/right/up/down 0/0/0/0\n",
        encoding="utf-8",
    )

    failures = smoke_check_log(str(log), project_id="tiny_runner")

    assert "missing serial text: Bundled project: tiny_runner" in failures

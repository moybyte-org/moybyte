from moybyte_cli.main import main


def test_cli_doctor(capsys):
    result = main(["doctor"])
    captured = capsys.readouterr()

    assert "Moybyte doctor" in captured.out
    assert "python: " in captured.out
    # Doctor reports on THIS environment, so its exit code depends on it: 0 when
    # everything `make setup` installs is importable, 1 with a remedy line when
    # something the docs promise is missing (asserting a hard 0 would make the
    # suite pass only on a fully-provisioned machine).
    if "MISSING" in captured.out:
        assert result == 1
        assert "make setup" in captured.out
    else:
        assert result == 0


def test_cli_validate(capsys):
    result = main(["validate", "examples/tiny_runner.moyproj"])
    captured = capsys.readouterr()

    assert result == 0
    assert "valid: Tiny Runner" in captured.out


def test_cli_new_creates_project(tmp_path, capsys):
    project = tmp_path / "space game"
    result = main(["new", str(project), "--title", "Space Game"])
    captured = capsys.readouterr()
    project_dir = tmp_path / "space game.moyproj"

    assert result == 0
    assert "created:" in captured.out
    assert (project_dir / "manifest.json").exists()
    assert (project_dir / "main.py").exists()

    validate_result = main(["validate", str(project_dir)])
    assert validate_result == 0


def test_cli_new_rejects_existing_project(tmp_path, capsys):
    project = tmp_path / "taken"
    assert main(["new", str(project)]) == 0

    result = main(["new", str(project)])
    captured = capsys.readouterr()

    assert result == 2
    assert "project already exists" in captured.err


def test_cli_compile(capsys):
    result = main(["compile", "examples/blocks_demo.moyproj"])
    captured = capsys.readouterr()

    assert result == 0
    assert "generated:" in captured.out


def test_cli_run_headless(capsys):
    result = main(["run", "examples/tiny_runner.moyproj", "--headless", "--frames", "3"])
    captured = capsys.readouterr()

    assert result == 0
    assert "frames=3" in captured.out


def test_cli_run_accepts_fps_for_headless(capsys):
    result = main(["run", "examples/tiny_runner.moyproj", "--headless", "--frames", "3", "--fps", "15"])
    captured = capsys.readouterr()

    assert result == 0
    assert "frames=3" in captured.out


def test_cli_run_rejects_invalid_window_options(capsys):
    result = main(["run", "examples/tiny_runner.moyproj", "--headless", "--frames", "0"])
    captured = capsys.readouterr()

    assert result == 2
    assert "--frames must be greater than zero" in captured.err


def test_cli_check_portable(capsys):
    result = main(["check-portable", "examples/tiny_runner.moyproj"])
    captured = capsys.readouterr()

    assert result == 0
    assert "portable check passed" in captured.out


def test_cli_firmware_header(capsys, tmp_path):
    out = tmp_path / "bundle.h"
    result = main(
        [
            "firmware-header",
            "examples/tiny_runner.moyproj",
            "--board",
            "lilygo_t_deck_plus",
            "--out",
            str(out),
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "generated:" in captured.out
    assert out.exists()


def test_cli_firmware_smoke_check(capsys, tmp_path):
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

    result = main(["firmware-smoke-check", str(log)])
    captured = capsys.readouterr()

    assert result == 0
    assert "firmware smoke check passed" in captured.out


def test_cli_lilygo_next_reports_status(capsys):
    result = main(["lilygo-next"])
    captured = capsys.readouterr()

    assert result in [0, 1, 2]
    assert "Moybyte LilyGO next step" in captured.out

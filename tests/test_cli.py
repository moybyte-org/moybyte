from kidcode_cli.main import main


def test_cli_doctor(capsys):
    result = main(["doctor"])
    captured = capsys.readouterr()

    assert result == 0
    assert "KidCode doctor" in captured.out


def test_cli_validate(capsys):
    result = main(["validate", "examples/tiny_runner.kcproj"])
    captured = capsys.readouterr()

    assert result == 0
    assert "valid: Tiny Runner" in captured.out


def test_cli_new_creates_project(tmp_path, capsys):
    project = tmp_path / "space game"
    result = main(["new", str(project), "--title", "Space Game"])
    captured = capsys.readouterr()
    project_dir = tmp_path / "space game.kcproj"

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
    result = main(["compile", "examples/blocks_demo.kcproj"])
    captured = capsys.readouterr()

    assert result == 0
    assert "generated:" in captured.out


def test_cli_run_headless(capsys):
    result = main(["run", "examples/tiny_runner.kcproj", "--headless", "--frames", "3"])
    captured = capsys.readouterr()

    assert result == 0
    assert "frames=3" in captured.out


def test_cli_run_accepts_fps_for_headless(capsys):
    result = main(["run", "examples/tiny_runner.kcproj", "--headless", "--frames", "3", "--fps", "15"])
    captured = capsys.readouterr()

    assert result == 0
    assert "frames=3" in captured.out


def test_cli_run_rejects_invalid_window_options(capsys):
    result = main(["run", "examples/tiny_runner.kcproj", "--headless", "--frames", "0"])
    captured = capsys.readouterr()

    assert result == 2
    assert "--frames must be greater than zero" in captured.err


def test_cli_check_portable(capsys):
    result = main(["check-portable", "examples/tiny_runner.kcproj"])
    captured = capsys.readouterr()

    assert result == 0
    assert "portable check passed" in captured.out

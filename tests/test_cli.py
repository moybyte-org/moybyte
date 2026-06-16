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

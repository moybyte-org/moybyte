from kidcode_cli.main import main
from kidcode_cli.portable import check_source


def test_portable_checker_accepts_kidcode_math_random():
    issues = check_source(
        "from kidcode import *\nimport math\nfrom random import randint\n",
        "ok.py",
    )

    assert issues == []


def test_portable_checker_rejects_pc_only_imports():
    issues = check_source("import os\nimport pygame\n", "bad.py")

    assert len(issues) == 2
    assert "os" in issues[0].message
    assert "pygame" in issues[1].message


def test_portable_checker_rejects_direct_open_call():
    issues = check_source("from kidcode import *\nopen('save.txt')\n", "bad.py")

    assert len(issues) == 1
    assert "open" in issues[0].message


def test_cli_check_portable_examples(capsys):
    result = main(
        [
            "check-portable",
            "examples/tiny_runner.kcproj",
            "examples/blocks_demo.kcproj",
            "examples/music_player_stub.kcproj",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "portable check passed" in captured.out

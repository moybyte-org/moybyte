"""The browser console's baked `.gz` is zopfli's, and reproducible.

tools/zopfli_gz.py replaced `gzip -9` in firmware/web_runner/build.sh (image
headroom). The blob check inflates a `.gz` to compare it, so what has to hold
here is that the stream is plain gzip (what the webhost's `Content-Encoding:
gzip` needs), that it carries no mtime or name, that the same input gives the
same bytes, and that build.sh does not go back to another compressor.
"""
import gzip
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import zopfli_gz  # noqa: E402

needs_zopfli = pytest.mark.skipif(
    importlib.util.find_spec("zopfli") is None,
    reason="pip install 'zopfli==0.4.3' (the dev extra)")

SAMPLE = (b"export const a = 1;\n" * 400) + bytes(range(256)) * 20


@needs_zopfli
def test_plain_gzip_with_no_mtime_or_name():
    out = zopfli_gz.gz(SAMPLE)
    assert gzip.decompress(out) == SAMPLE
    # magic, deflate, FLG=0 (no name/extra/comment), MTIME=0
    assert out[:8] == b"\x1f\x8b\x08\x00\x00\x00\x00\x00"
    assert len(out) < len(gzip.compress(SAMPLE, 9, mtime=0))


@needs_zopfli
def test_same_input_same_bytes():
    assert zopfli_gz.gz(SAMPLE) == zopfli_gz.gz(SAMPLE)


@needs_zopfli
def test_main_writes_the_gz_beside_the_file(tmp_path, capsys):
    f = tmp_path / "a.mjs"
    f.write_bytes(SAMPLE)
    zopfli_gz.main([str(f)])
    assert gzip.decompress((tmp_path / "a.mjs.gz").read_bytes()) == SAMPLE
    assert not (tmp_path / "a.mjs.gz.tmp").exists()
    assert "a.mjs" in capsys.readouterr().out


def test_missing_zopfli_is_an_error_not_a_fallback(monkeypatch):
    monkeypatch.setitem(sys.modules, "zopfli", None)
    monkeypatch.setitem(sys.modules, "zopfli.gzip", None)
    with pytest.raises(SystemExit) as e:
        zopfli_gz.gz(SAMPLE)
    assert "zopfli" in str(e.value)


def test_build_sh_compresses_with_zopfli_only():
    sh = (ROOT / "firmware/web_runner/build.sh").read_text()
    assert "tools/zopfli_gz.py" in sh
    assert not re.search(r"^\s*gzip\s", sh, re.M), "build.sh must not gzip by hand"


def test_the_pin_is_one_version_everywhere():
    pins = set()
    for p in [ROOT / "pyproject.toml", *(ROOT / ".github/workflows").glob("*.yml"),
              ROOT / "tools/zopfli_gz.py"]:
        pins |= set(re.findall(r"zopfli==([0-9][0-9.a-z]*)", p.read_text()))
    assert len(pins) == 1, pins

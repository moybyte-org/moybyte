"""tools/ci_image: the container preflight runs the host suite in, held to
CI's host job, and preflight's refusal to fall back without it."""

import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "tools" / "ci_image" / "Dockerfile").read_text()
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text()


def test_the_image_is_the_runners_os_pinned_by_digest():
    host_job = CI.split("\n  test:\n")[1].split("\n  classify-hardware-impact:")[0]
    assert "runs-on: ubuntu-latest" in host_job     # ubuntu-26.04, by the job logs
    assert re.search(r"^FROM ubuntu:26\.04@sha256:[0-9a-f]{64}$", DOCKERFILE, re.M)


def test_the_images_python_is_in_cis_matrix():
    matrix = re.search(r'python-version: \[([^\]]*)\]', CI).group(1)
    versions = re.findall(r'"(3\.\d+)"', matrix)
    (pinned,) = set(re.findall(r"^ARG PYTHON=(3\.\d+)\.\d+$", DOCKERFILE, re.M))
    assert pinned in versions
    assert "SYSTEM_PYTHON=python%s" % pinned in \
        (ROOT / "tools" / "ci_image" / "run.sh").read_text()


def test_the_image_installs_what_ci_installs():
    for apt in re.findall(r"apt-get install -y --no-install-recommends ([\w -]+)", CI):
        for pkg in apt.split():
            assert re.search(r"\b%s\b" % re.escape(pkg), DOCKERFILE), pkg


def _path_without_docker(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "dirname", "mktemp", "rm", "env", "cat"):
        os.symlink(shutil.which(tool), bin_dir / tool)
    return str(bin_dir)


def test_preflight_fails_without_docker_rather_than_running_natively(tmp_path):
    env = dict(os.environ, PATH=_path_without_docker(tmp_path))
    r = subprocess.run(["bash", str(ROOT / "tools" / "preflight.sh"), "--full"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "no docker" in r.stderr and "--native" in r.stderr
    assert "==" not in r.stdout, "a step ran before the refusal"


def test_preflights_host_suite_steps_run_in_the_container():
    text = (ROOT / "tools" / "preflight.sh").read_text()
    for name in ("suite (redraw excluded)", "redraw suite, alone",
                 "desktop MicroPython with the native usermods",
                 "tests that name a changed document"):
        line = re.search(r'step "%s"(?:[^\n]*\\\n)*[^\n]*' % re.escape(name), text).group(0)
        assert "${IN[@]}" in line or "${PYTEST[@]}" in line, name
    assert 'PYTEST=("${IN[@]}"' in text

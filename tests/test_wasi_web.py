"""The browser's WASI commands (native/moy_wasm_web/moy_wasi_web.h): the
boards' adapter, native/moy_wasm/moy_wasi.c, compiled for the site's build
over WAMR's names for the page's engine. These hold the seam where a change
to the adapter would otherwise first fail in a `build.sh --site`: every WAMR
name it calls is one the page's engine answers, and every header of its own
it includes is staged beside it. tests/test_web_term_e2e.py runs a command
in real Chrome."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WASM = ROOT / "native" / "moy_wasm"
WEB = ROOT / "native" / "moy_wasm_web"


def _web_branch(text):
    """`text` as MOY_WASI_WEB compiles it: an `#ifdef MOY_WASI_WEB` block's
    `#else` half dropped."""
    return re.sub(r"(#ifdef MOY_WASI_WEB\n.*?)#else\n.*?#endif", r"\1#endif", text, flags=re.S)


def _wamr_names(text):
    return set(re.findall(r"\b(wasm_(?:runtime|memory)_[a-z_]+)\s*\(", text))


def test_every_wamr_name_the_adapter_calls_is_the_page_engine_s():
    used = set()
    for f in ("moy_wasi.c", "moy_wasi_os_rows.h"):
        used |= _wamr_names(_web_branch((WASM / f).read_text()))
    header = (WEB / "moy_wasi_web.h").read_text()
    body = (WEB / "moy_wasi_web.c").read_text()
    declared = _wamr_names(header)
    defined = set(re.findall(r"^\S[^;\n]*?\b(wasm_(?:runtime|memory)_[a-z_]+)\(", body, re.M))
    assert used <= declared, sorted(used - declared)
    assert used <= defined, sorted(used - defined)


def test_the_adapter_s_own_headers_are_staged_beside_it():
    staged = re.search(r"for f in ([^;]*); do\n\s*cp \"\$\{REPO_ROOT\}/native/moy_wasm/",
                       (ROOT / "firmware" / "web_runner" / "build.sh").read_text())
    assert staged, "build.sh stages no native/moy_wasm files beside moy_wasm_web"
    copied = set(staged.group(1).split())
    assert "moy_wasi.c" in copied
    for src in ("moy_wasi.c", "moy_wasi.h"):
        for inc in re.findall(r'#include "([^"]+)"', (WASM / src).read_text()):
            if (WASM / inc).exists():
                assert inc in copied, "%s includes %s, which build.sh does not stage" % (src, inc)


def test_only_the_site_s_build_compiles_the_adapter():
    """The bundle every board bakes runs no shell, so it carries no WASI
    (its names absent, never stubs: .claude/rules/web.md)."""
    mk = (WEB / "micropython.mk").read_text()
    guarded = re.search(r"ifeq \(\$\(MOY_WEB_SHELL\),1\)\n(.*?)\nendif", mk, re.S)
    assert guarded and "moy_wasi.c" in guarded.group(1) and "MOY_WASI_WEB" in guarded.group(1)
    rules = [ln for ln in mk.splitlines() if not ln.startswith("#")]
    assert sum("moy_wasi.c" in ln for ln in rules) == 1

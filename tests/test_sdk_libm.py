"""The SDK's libm (sdk/libc/m/, sdk/README.md): on RISC-V a module takes it
in place of the toolchain's, whose objects address their constants
absolutely.

- a module calling <math.h>, float and double, links for each board chip,
  imports nothing, carries only relocations its chip's loader applies, and
  passes the console's verdict;
- the libm objects in the P4's libmoyos hold no writable data and reach for
  no errno or reentrancy record;
- the same objects, run on a RISC-V stand-in (qemu's user mode), answer as
  glibc does to within 2 ulp on every row of the fixture;
- built for the host, its functions sweep within an ulp of glibc's (2 for
  the hyperbolics, log10 and asinf);
- the copy is MicroPython's lib/libm and lib/libm_dbl, file for file.
"""

import glob
import os
import re
import shutil
import subprocess

import pytest

from runtime import native_build
from tests.test_os_table import MACHINE, _table, check  # noqa: F401 -- the fixture
from tools import elf_cart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATH = os.path.join(ROOT, "tests", "fixtures", "elf", "elf_math.moy")
LIBM = os.path.join(ROOT, "sdk", "libc", "m")
ARCH = {"esp32s3": "xtensa", "esp32p4": "riscv"}
# The toolchain's files the copy leaves out: Thumb assembly, and the float
# trigonometry sdk/libc/m/trigf.c replaces.
LEFT_OUT = {"libm": {"thumb_vfp_sqrtf.c", "sf_sin.c", "sf_cos.c", "sf_tan.c"},
            "libm_dbl": {"thumb_vfp_sqrt.c"}}


def _readelf(chip):
    strip = elf_cart.toolchain(chip)[2]
    return strip[:-len("strip")] + "readelf"


def _applied(chip):
    """The relocation types the chip's loader applies: its relocate's cases,
    and NONE, which it skips."""
    arch = ARCH[chip]
    src = open(os.path.join(ROOT, "native", "moy_elf", "elf_loader", "src", "arch",
                            "esp_elf_%s.c" % arch)).read()
    return set(re.findall(r"case (R_\w+):", src)) | {"R_%s_NONE" % arch.upper()}


@pytest.mark.parametrize("chip", sorted(ARCH))
def test_a_module_calling_libm_links_and_loads(chip, check, tmp_path):
    if not elf_cart.available(chip):
        pytest.skip("no %s toolchain" % chip)
    src = sorted(glob.glob(os.path.join(MATH, "src", "*.c")))
    mod = elf_cart.build_module(src, chip, str(tmp_path / "m.elf"), signed=False)
    rows = len(_table()["rows"])
    why, _info = check(open(mod, "rb").read(), MACHINE[chip], rows)
    assert why is None
    readelf = _readelf(chip)
    syms = subprocess.run([readelf, "--dyn-syms", "-W", mod], capture_output=True,
                          text=True, check=True).stdout
    assert [ln for ln in syms.splitlines() if re.search(r"\sUND\s+\S+$", ln)] == []
    rel = subprocess.run([readelf, "-r", "-W", mod], capture_output=True, text=True,
                         check=True).stdout
    types = set(re.findall(r"\s(R_[A-Z0-9_]+)\s", rel))
    assert types and types <= _applied(chip), types - _applied(chip)
    dyn = subprocess.run([readelf, "-d", "-W", mod], capture_output=True, text=True,
                         check=True).stdout
    assert "TEXTREL" not in dyn


def _members(tmp_path):
    """The libm objects of the P4's libmoyos, extracted into tmp_path."""
    if not elf_cart.available("esp32p4"):
        pytest.skip("no esp32p4 toolchain")
    lib = elf_cart.libmoyos("esp32p4")
    _cc, ar, _strip = elf_cart.toolchain("esp32p4")
    names = [n for n in subprocess.run([ar, "t", lib], capture_output=True, text=True,
                                       check=True).stdout.split() if n.startswith("m_")]
    assert names
    subprocess.run([ar, "x", lib] + names, cwd=str(tmp_path), check=True)
    return [str(tmp_path / n) for n in names]


def test_the_p4_libm_holds_no_state(tmp_path):
    objs = _members(tmp_path)
    readelf = _readelf("esp32p4")
    nm = readelf[:-len("readelf")] + "nm"
    for o in objs:
        secs = subprocess.run([readelf, "-S", "-W", o], capture_output=True, text=True,
                              check=True).stdout
        for m in re.finditer(r"\]\s+(\S+)\s+(PROGBITS|NOBITS)\s+\S+\s+\S+\s+(\S+)\s+\S+\s+(\S*)",
                             secs):
            name, size, flags = m.group(1), int(m.group(3), 16), m.group(4)
            assert not ("W" in flags and "A" in flags and size), (o, name)
        undef = set(subprocess.run([nm, "-u", o], capture_output=True, text=True,
                                   check=True).stdout.split()) - {"U"}
        assert not undef & {"__errno", "__getreent", "_impure_ptr", "_global_impure_ptr",
                            "signgam"}, (o, undef)


def test_the_p4_libm_answers_as_glibc_on_a_riscv_stand_in(tmp_path):
    qemu = shutil.which("qemu-riscv32-static") or shutil.which("qemu-riscv32")
    if qemu is None:
        pytest.skip("no qemu-riscv32")
    objs = _members(tmp_path)
    cc = elf_cart.toolchain("esp32p4")[0]
    reent = tmp_path / "reent.c"
    # newlib's stdio for the stand-in's printf: the console's modules have the SDK's.
    reent.write_text("#include <reent.h>\nstatic struct _reent r = _REENT_INIT(r);\n"
                     "struct _reent *__getreent(void) { return &r; }\n")
    exe = str(tmp_path / "math.rv")
    subprocess.run([cc] + elf_cart.CHIPS["esp32p4"]["flags"]
                   + ["-Os", "--specs=sim.specs", "-o", exe,
                      os.path.join(MATH, "src", "math.c"), str(reent)] + objs + ["-lm"],
                   check=True, capture_output=True)
    r = subprocess.run([qemu, exe], capture_output=True, text=True, timeout=120)
    n = len(re.findall(r"^\s+\{", open(os.path.join(MATH, "src", "rows.h")).read(), re.M))
    assert (r.returncode, r.stdout) == (0, "math: %d of %d within 2 ulp of glibc\n" % (n, n))


def test_the_fixture_s_rows_are_this_machine_s_glibc(tmp_path):
    cc = native_build.cc()
    if cc is None:
        pytest.skip("no C compiler")
    exe = str(tmp_path / "math")
    subprocess.run([cc, "-O1", "-o", exe, os.path.join(MATH, "src", "math.c"), "-lm"],
                   check=True)
    r = subprocess.run([exe], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


SWEEP = r"""
#include <dlfcn.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static uint64_t rng = 0x9e3779b97f4a7c15ull;
static double rd(double lo, double hi) {
    rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17;
    return lo + (hi - lo) * ((rng >> 11) * 0x1p-53);
}
static int64_t dk(double x) { int64_t i; memcpy(&i, &x, 8); return i < 0 ? INT64_MIN - i : i; }
static int32_t fk(float x) { int32_t i; memcpy(&i, &x, 4); return i < 0 ? INT32_MIN - i : i; }
static double du(double a, double b) {
    if (isnan(a) || isnan(b)) return isnan(a) && isnan(b) ? 0 : 1e9;
    int64_t d = dk(a) - dk(b); return d < 0 ? -(double)d : (double)d;
}
static double fu(float a, float b) {
    if (isnan(a) || isnan(b)) return isnan(a) && isnan(b) ? 0 : 1e9;
    int64_t d = (int64_t)fk(a) - fk(b); return d < 0 ? -(double)d : (double)d;
}
static void *m;
enum { N = 20000 };
#define D1(n, lo, hi) { double (*g)(double) = dlsym(m, #n); double w = 0; \
  for (int i = 0; i < N; i++) { double x = rd(lo, hi); if (i & 1) x = -x; \
    double u = du(n(x), g(x)); if (u > w) w = u; } printf("%s %.0f\n", #n, w); }
#define D2(n, lo, hi, lo2, hi2) { double (*g)(double, double) = dlsym(m, #n); double w = 0; \
  for (int i = 0; i < N; i++) { double x = rd(lo, hi), y = rd(lo2, hi2); if (i & 2) y = -y; \
    double u = du(n(x, y), g(x, y)); if (u > w) w = u; } printf("%s %.0f\n", #n, w); }
#define F1(n, lo, hi) { float (*g)(float) = dlsym(m, #n); double w = 0; \
  for (int i = 0; i < N; i++) { float x = rd(lo, hi); if (i & 1) x = -x; \
    double u = fu(n(x), g(x)); if (u > w) w = u; } printf("%s %.0f\n", #n, w); }
#define F2(n, lo, hi, lo2, hi2) { float (*g)(float, float) = dlsym(m, #n); double w = 0; \
  for (int i = 0; i < N; i++) { float x = rd(lo, hi), y = rd(lo2, hi2); if (i & 2) y = -y; \
    double u = fu(n(x, y), g(x, y)); if (u > w) w = u; } printf("%s %.0f\n", #n, w); }

int main(void) {
    m = dlopen("libm.so.6", RTLD_NOW);
    if (!m) return 2;
    D1(sin, 0, 1e4) D1(cos, 0, 1e22) D1(tan, 0, 1e300) D1(sqrt, 0, 1e300) D1(exp, 0, 709)
    D1(log, 0, 1e300) D1(floor, 0, 1e17) D1(ceil, 0, 1e17) D1(fabs, 0, 1e300)
    D1(asin, 0, 1) D1(acos, 0, 1) D1(atan, 0, 1e3) D1(round, 0, 1e17) D1(trunc, 0, 1e17)
    D1(log10, 0, 1e300) D1(sinh, 0, 700) D1(cosh, 0, 700) D1(tanh, 0, 20)
    D2(pow, 0, 10, 0, 300) D2(fmod, 0, 1e300, 0, 100) D2(atan2, -10, 10, 0, 1e3)
    F1(sinf, 0, 1e4) F1(cosf, 0, 1e30) F1(tanf, 0, 1e38) F1(sqrtf, 0, 1e38) F1(expf, 0, 88)
    F1(logf, 0, 1e38) F1(floorf, 0, 1e8) F1(ceilf, 0, 1e8) F1(fabsf, 0, 1e38)
    F1(acosf, 0, 1) F1(atanf, 0, 1e3) F1(roundf, 0, 1e8) F1(truncf, 0, 1e8)
    F1(asinf, 0, 1) F1(log10f, 0, 1e38) F1(sinhf, 0, 88) F1(coshf, 0, 88) F1(tanhf, 0, 20)
    F2(powf, 0, 10, 0, 38) F2(fmodf, 0, 1e38, 0, 100) F2(atan2f, -10, 10, 0, 1e3)
    return 0;
}
"""

# glibc and the copy round a few last bits apart here.
TWO_ULP = {"asinf", "log10", "log10f", "sinh", "sinhf", "cosh", "coshf", "tanh", "tanhf"}


def test_the_libm_sweeps_within_an_ulp_of_glibc(tmp_path):
    """sdk/libc/m built for the host (glibc's math.h names its own __sin,
    __cos and __tan, and a C23 one sinpi, so the copy's take other names here;
    fdlibm's __int32_t is sys/types.h's), against glibc's
    own functions, looked up by name in libm.so.6."""
    cc = native_build.cc()
    if cc is None or not os.path.exists("/usr/include/dlfcn.h"):
        pytest.skip("no host C compiler with glibc")
    shim = tmp_path / "shim.h"
    shim.write_text("#include <sys/types.h>\n#include <math.h>\n#define __sin lc_m__sin\n#define __cos lc_m__cos\n"
                    "#define __tan lc_m__tan\n#define sinpi lc_m_sinpi\n")
    objs = []
    for part, inc in (("libm", "libm"), ("libm_dbl", "libm_dbl"), ("", "libm")):
        for src in sorted(glob.glob(os.path.join(LIBM, part, "*.c"))):
            o = str(tmp_path / ("%s_%s.o" % (part or "lc", os.path.basename(src)[:-2])))
            subprocess.run([cc, "-O1", "-ffp-contract=off", "-w", "-include", str(shim),
                            "-I", os.path.join(LIBM, inc), "-c", src, "-o", o], check=True)
            objs.append(o)
    drv = tmp_path / "sweep.c"
    drv.write_text(SWEEP)
    exe = str(tmp_path / "sweep")
    subprocess.run([cc, "-O1", "-w", "-o", exe, str(drv)] + objs + ["-ldl"], check=True)
    r = subprocess.run([exe], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout
    worst = {name: int(u) for name, u in (ln.split() for ln in r.stdout.splitlines())}
    assert len(worst) == 42
    over = {n: u for n, u in worst.items() if u > (2 if n in TWO_ULP else 1)}
    assert over == {}


def _micropython():
    for d in sorted(glob.glob(os.path.join(ROOT, "firmware", "*", ".build", "micropython"))
                    + [os.path.join(ROOT, ".build", "unix_micropython", "micropython")]):
        if os.path.isdir(os.path.join(d, "lib", "libm_dbl")):
            return d
    return None


def test_the_copy_is_micropython_s():
    mp = _micropython()
    if mp is None:
        pytest.skip("no MicroPython tree (a board build fetches one)")
    for part, left in LEFT_OUT.items():
        theirs = set(os.listdir(os.path.join(mp, "lib", part))) - left
        ours = set(os.listdir(os.path.join(LIBM, part)))
        assert ours == theirs, part
        for name in sorted(ours):
            with open(os.path.join(LIBM, part, name), "rb") as a, \
                    open(os.path.join(mp, "lib", part, name), "rb") as b:
                assert a.read() == b.read(), os.path.join(part, name)

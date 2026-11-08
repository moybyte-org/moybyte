"""The SDK's C library (sdk/libc/, sdk/README.md): unmodified POSIX programs
run as native commands and answer as the host's own C library does; its
printf and RISC-V helpers are bit-exact against the host's; the board
modules link for both chips; the sysroot's network values are lwIP's."""

import glob
import os
import re
import subprocess

import pytest

from runtime import native_build
from tests.test_elf_commands import built, out, run, term  # noqa: F401 -- the fixtures

if native_build.cc() is None:
    pytest.skip("no C compiler", allow_module_level=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "elf")
LIBC = os.path.join(ROOT, "sdk", "libc")
SYSROOT = os.path.join(ROOT, "sdk", "sysroot", "include")


def _native(tmp_path, name, src):
    exe = str(tmp_path / name)
    subprocess.run([native_build.cc(), "-O1", "-w", "-o", exe, src], check=True)
    return exe


def test_the_libc_answers_as_the_host_s_own_does(term, tmp_path):
    """printf's conversions, the number parsers, sscanf, the heap, qsort,
    ctype, strerror, getopt, getline and the flush at exit: the module's
    stdout byte for byte the same program's built against glibc."""
    _ws, app = term
    args = "-a -b x -a op1 op2"
    run(app, "echo 'first line' | elf.libc/libc %s > /files/out.txt" % args)
    have = (tmp_path / "files" / "out.txt").read_bytes()
    exe = _native(tmp_path, "libc", os.path.join(FIXTURES, "elf_libc.moy", "src", "libc.c"))
    want = subprocess.run([exe] + args.split(), input=b"first line\n", capture_output=True,
                          check=True).stdout
    assert have == want
    assert have.endswith(b"unflushed at exit")


def test_head_reads_files_through_stdio(term, tmp_path):
    _ws, app = term
    run(app, "cd /files/Documents; printf '1\\n2\\n3\\n' > a.txt; printf 'x\\n' > b.txt")
    run(app, "cd /files/Documents; elf.head/head -n 2 a.txt b.txt > o.txt; echo $? > s.txt")
    assert (tmp_path / "files" / "Documents" / "o.txt").read_text() == "==> a.txt <==\n1\n2\n\n==> b.txt <==\nx\n"
    assert (tmp_path / "files" / "Documents" / "s.txt").read_text() == "0\n"
    run(app, "cd /files/Documents; printf 'p\\nq\\n' | elf.head/head -n 1 > o.txt")
    assert (tmp_path / "files" / "Documents" / "o.txt").read_text() == "p\n"
    got = out(app, "cd /files/Documents; elf.head/head -n x a.txt; echo status $?")
    assert "".join(got[:-1]) == "head: invalid number of lines: 'x'" and got[-1] == "status 1"
    got = out(app, "cd /files/Documents; elf.head/head nope.txt; echo status $?")
    assert "".join(got[:-1]) == (
        "head: cannot open 'nope.txt' for reading: No such file or directory")


PRINTF_DRIVER = r"""
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef int (*lc_out_fn)(void *arg, const char *p, size_t n);
int lc_format(lc_out_fn out, void *arg, const char *fmt, va_list ap);
__attribute__((visibility("hidden"))) size_t lc_write(void *f, const void *p, size_t n) {
    (void)f; (void)p; return n;
}
typedef struct { char *p; size_t n; } m_t;
static int o(void *a, const char *p, size_t n) {
    m_t *m = a; memcpy(m->p + m->n, p, n); m->n += n; return 0;
}
static void mine(char *buf, const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); m_t m = { buf, 0 }; lc_format(o, &m, fmt, ap);
    buf[m.n] = 0; va_end(ap);
}
static uint64_t rng = 0x9E3779B97F4A7C15ULL;
static uint64_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; }
int main(void) {
    const char *fmts[] = { "%f", "%.0f", "%.1f", "%.2f", "%.3f", "%.10f", "%e", "%.0e", "%.3e",
        "%.15e", "%g", "%.1g", "%.3g", "%.10g", "%.17g", "%#g", "%#.0f", "%#.0e", "%10.4f",
        "%-12.3e", "%+g", "%.20f", "%.40e", "%d", "%5.3d", "%-+8lld", "%#x", "%#o", "%.0d" };
    int nf = sizeof fmts / sizeof fmts[0];
    long bad = 0;
    char a[2000], b[2000];
    for (long i = 0; i < 60000; i++) {
        double x; uint64_t u = rnd();
        if (i % 4 == 0) u = (u & 0x800FFFFFFFFFFFFFULL) | ((uint64_t)(1023 - 30 + rnd() % 60) << 52);
        if (i % 7 == 0) x = (double)(rnd() % 100000) / (double)(1 + rnd() % 1000);
        else if (i % 11 == 0) x = (double)(rnd() % 2000) / 8.0 - 100;
        else memcpy(&x, &u, 8);
        if (x != x) continue;
        const char *f = fmts[i % nf];
        if (strchr("dxo", f[strlen(f) - 1])) {
            long long v = (long long)u >> (rnd() % 64);
            if (strstr(f, "ll")) { snprintf(a, sizeof a, f, v); mine(b, f, v); }
            else { snprintf(a, sizeof a, f, (int)v); mine(b, f, (int)v); }
        } else { snprintf(a, sizeof a, f, x); mine(b, f, x); }
        if (strcmp(a, b)) { if (bad++ < 5) printf("%s %a: [%s] [%s]\n", f, x, a, b); }
    }
    double sp[] = { 0.0, -0.0, 0.5, 1.5, 2.5, 0.05, 0.15, 2.675, 1.005, 1e20, 9.9999999,
        0.99999, 5e-324, 1.7976931348623157e308, 1e300, 1e-300, 1e-5, 0.0001, 1.0/0.0 };
    for (unsigned i = 0; i < sizeof sp / sizeof sp[0]; i++)
        for (int j = 0; j < nf; j++) {
            if (strchr("dxo", fmts[j][strlen(fmts[j]) - 1])) continue;
            snprintf(a, sizeof a, fmts[j], sp[i]); mine(b, fmts[j], sp[i]);
            if (strcmp(a, b)) { if (bad++ < 10) printf("%s %a: [%s] [%s]\n", fmts[j], sp[i], a, b); }
        }
    printf("bad %ld\n", bad);
    return bad != 0;
}
"""

FAMILY = ("printf fprintf vprintf vfprintf sprintf vsprintf snprintf vsnprintf asprintf "
          "vasprintf dprintf vdprintf").split()


def test_printf_is_glibc_s_to_the_bit(tmp_path):
    """The formatter, built for the host with its family renamed, against
    glibc's snprintf over random doubles and integers."""
    cc = native_build.cc()
    drv = tmp_path / "drv.c"
    drv.write_text(PRINTF_DRIVER)
    obj = str(tmp_path / "lcp.o")
    subprocess.run([cc, "-O1", "-w", "-c", "-I", LIBC] + ["-D%s=t_%s" % (f, f) for f in FAMILY]
                   + [os.path.join(LIBC, "lc_printf.c"), "-o", obj], check=True)
    exe = str(tmp_path / "drv")
    subprocess.run([cc, "-O1", "-w", "-o", exe, str(drv), obj], check=True)
    r = subprocess.run([exe], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


RV32_DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
double lc_test___divdf3(double, double);
uint64_t lc_test___udivdi3(uint64_t, uint64_t); uint64_t lc_test___umoddi3(uint64_t, uint64_t);
int64_t lc_test___divdi3(int64_t, int64_t); int64_t lc_test___moddi3(int64_t, int64_t);
float lc_test___floatdisf(int64_t); float lc_test___floatundisf(uint64_t);
uint64_t lc_test___fixunssfdi(float); int lc_test___clzsi2(uint32_t);
double lc_test___powidf2(double, int);
static uint64_t rng = 88172645463325252ULL;
static uint64_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; }
static double d_of(uint64_t u) { double d; memcpy(&d, &u, 8); return d; }
static uint64_t u_of(double d) { uint64_t u; memcpy(&u, &d, 8); return u; }
static int same(double a, double b) { return (a != a && b != b) || u_of(a) == u_of(b); }
int main(void) {
    long bad = 0;
    double sp[] = { 0.0, -0.0, 1.0, -1.0, 3.0, 1e308, 1e-308, 4.9e-324, 2.2250738585072014e-308,
        1.0 / 0.0, -1.0 / 0.0, 0.0 / 0.0, 1e-300, 7.0, 0.1 };
    int ns = sizeof sp / sizeof sp[0];
    for (int i = 0; i < ns; i++)
        for (int j = 0; j < ns; j++)
            bad += !same(sp[i] / sp[j], lc_test___divdf3(sp[i], sp[j]));
    for (long i = 0; i < 300000; i++) {
        uint64_t x = rnd(), y = rnd();
        if (i % 3 == 0) x = (x & 0x800FFFFFFFFFFFFFULL) | ((uint64_t)(rnd() % 40) << 52);
        if (i % 5 == 0) y = (y & 0x800FFFFFFFFFFFFFULL) | ((uint64_t)(2000 + rnd() % 47) << 52);
        bad += !same(d_of(x) / d_of(y), lc_test___divdf3(d_of(x), d_of(y)));
        uint64_t p = rnd() >> (rnd() % 64), q = (rnd() >> (rnd() % 64)) | 1;
        bad += lc_test___udivdi3(p, q) != p / q || lc_test___umoddi3(p, q) != p % q;
        int64_t a = (int64_t)p * ((i & 1) ? -1 : 1), b = (int64_t)q * ((i & 2) ? -1 : 1);
        bad += lc_test___divdi3(a, b) != a / b || lc_test___moddi3(a, b) != a % b;
        bad += lc_test___floatundisf(p) != (float)p || lc_test___floatdisf(a) != (float)a;
        float f = (float)(rnd() >> (rnd() % 64));
        bad += lc_test___fixunssfdi(f) != (uint64_t)f;
        uint32_t c = (uint32_t)p;
        bad += c && lc_test___clzsi2(c) != __builtin_clz(c);
    }
    bad += lc_test___powidf2(2.0, 10) != 1024.0 || lc_test___powidf2(2.0, -2) != 0.25;
    printf("bad %ld\n", bad);
    return bad != 0;
}
"""


def test_the_riscv_helpers_are_the_hardware_s_to_the_bit(tmp_path):
    """lc_rv32.c's division, bit counts and conversions, built for the host
    under test names, against the host's own arithmetic."""
    cc = native_build.cc()
    drv = tmp_path / "drv.c"
    drv.write_text(RV32_DRIVER)
    exe = str(tmp_path / "drv")
    subprocess.run([cc, "-O1", "-DLC_RV32_TEST", "-o", exe, str(drv),
                    os.path.join(LIBC, "lc_rv32.c")], check=True)
    r = subprocess.run([exe], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("chip", ["esp32s3", "esp32p4"])
def test_the_programs_link_for_each_board(chip, tmp_path):
    """The unmodified programs -- netcat, head, the libc probe, cat --
    link into a module for each chip: no name left to import, and nothing
    of the toolchain's C library but its PURE members."""
    from tools import elf_cart
    if not elf_cart.available(chip):
        pytest.skip("no %s toolchain" % chip)
    for name in ("elf_nc", "elf_head", "elf_libc", "elf_cat"):
        srcs = sorted(glob.glob(os.path.join(FIXTURES, name + ".moy", "src", "*.c")))
        mod = elf_cart.build_module(srcs, chip, str(tmp_path / (name + ".elf")), signed=False)
        assert os.path.getsize(mod) > 0


def _defines(path, every=None):
    """A header's #defines: name -> its last value, and into `every` (when
    given) name -> each value it has (a header may define one per branch)."""
    out = {}
    for m in re.finditer(r"^\s*#\s*define\s+([A-Z_0-9]+)\s+(\S[^/\n]*)", open(path).read(), re.M):
        out[m.group(1)] = m.group(2).strip()
        if every is not None:
            every.setdefault(m.group(1), []).append(m.group(2).strip())
    return out


def _value(v, table):
    v = v.strip()
    while v.startswith("(") and v.endswith(")") and v.count("(") == 1:
        v = v[1:-1].strip()
    v = re.sub(r"^\((in_addr_t|u32_t)\)", "", v).strip()
    v = re.sub(r"(?<=[0-9a-fA-F])[UL]+$", "", v)
    if v in table:
        return _value(table[v], table)
    m = re.fullmatch(r"\(?1u?\s*<<\s*(\d+)\)?", v)
    if m:
        return 1 << int(m.group(1))
    try:
        return int(v, 0)
    except ValueError:
        return None


def _values(v, every, depth=0):
    """Every number `v` can be, a name standing for each of its values."""
    name = v.strip().strip("()").strip()
    if name in every and depth < 8:
        return set().union(*(_values(w, every, depth + 1) for w in every[name]))
    got = _value(v, {})
    return {got} if got is not None else set()


def _idf():
    for d in sorted(glob.glob(os.path.join(ROOT, "firmware", "*", ".build", "esp-idf"))):
        if os.path.isdir(os.path.join(d, "components", "lwip")):
            return d
    return None


def test_the_sysroot_s_values_are_lwip_s():
    """Every value sdk/sysroot/include defines that lwIP's or the IDF's
    headers define too is the same number (where a board's IDF is here)."""
    idf = _idf()
    if idf is None:
        pytest.skip("no ESP-IDF tree under firmware/*/.build")
    comp = os.path.join(idf, "components")
    theirs, every = {}, {}
    for rel in ("lwip/lwip/src/include/lwip/sockets.h", "lwip/lwip/src/include/lwip/netdb.h",
                "lwip/lwip/src/include/lwip/inet.h", "lwip/lwip/src/include/lwip/ip4_addr.h",
                "lwip/lwip/src/include/lwip/ip6_addr.h", "newlib/platform_include/sys/poll.h",
                "newlib/platform_include/net/if.h"):
        theirs.update(_defines(os.path.join(comp, rel), every))
    theirs.setdefault("IPADDR_ANY", "((u32_t)0x00000000UL)")
    theirs.setdefault("IPADDR_LOOPBACK", "((u32_t)0x7f000001UL)")
    checked = 0
    for path in glob.glob(os.path.join(SYSROOT, "**", "*.h"), recursive=True):
        if path.endswith("mman.h"):
            continue
        ours = _defines(path)
        for name, v in ours.items():
            if name.startswith("_MOY") or name not in theirs:
                continue
            want = set().union(*(_values(w, every) for w in every[name]))
            have = _value(v, ours)
            if not want or have is None:
                continue
            assert have in want, "%s: %s defines %s, lwIP %s" % (name, path, have, want)
            checked += 1
    assert checked > 40


def test_the_kernel_reads_mmap_s_values_as_the_sdk_writes_them():
    """moy_os.c's fallback for a board with no sys/mman.h is the SDK's."""
    ours = _defines(os.path.join(SYSROOT, "sys", "mman.h"))
    kernel = _defines(os.path.join(ROOT, "native", "moy_elf", "moy_os.c"))
    for name in ("PROT_READ", "PROT_WRITE", "PROT_EXEC", "MAP_SHARED", "MAP_PRIVATE",
                 "MAP_ANONYMOUS"):
        assert _value(kernel[name], kernel) == _value(ours[name], ours), name

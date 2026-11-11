"""The U-mode lazy FPU rule (native/moy_elf/moy_umode.h, moy_u_fpu_lazy),
compiled from the header the P4 boards build and run here.

ESP-IDF turns the FPU off on every interrupt and context switch and back on
when the next FPU instruction traps. An interrupt between that enable and the
instruction's retry makes the same instruction trap again with the FPU off,
which must go to the enable again, not end the process: the decision is the
FPU state at the trap, never the instruction's history."""

import subprocess
from pathlib import Path

import pytest

from runtime.host import native_build

CC = native_build.cc()
if CC is None:
    pytest.skip("no C compiler", allow_module_level=True)

HEADER_DIR = Path(__file__).resolve().parent.parent / "native" / "moy_elf"

FS_OFF = 0x00001880          # MPP=M, MPIE, FS=Off
FS_ON = FS_OFF | (1 << 13)   # FS=Initial, what IDF's enable sets
FS_DIRTY = FS_OFF | (3 << 13)

CSRR_FRM = 0x002027F3        # csrr a5, frm
CSRR_FFLAGS = 0x001027F3
CSRR_FCSR = 0x003027F3
CSRR_MCYCLE = 0xB00027F3     # not an FPU CSR
FLW = 0x0005A787             # flw fa5, 0(a1)
FADD = 0x00F7F7D3            # fadd.s fa5, fa5, fa5
C_FLWSP = 0x6782             # c.flwsp fa5, 0(sp)
HWLP = 0x0000002B            # a hardware-loop opcode
EXT_ILL_FPU, EXT_ILL_PIE = 1, 4


@pytest.fixture(scope="module")
def lazy(tmp_path_factory):
    d = tmp_path_factory.mktemp("umode")
    src = d / "lazy.c"
    src.write_text(
        '#include <stdio.h>\n#include <stdlib.h>\n#include "moy_umode.h"\n'
        "int main(int argc, char **argv) {\n"
        "    for (int i = 1; i + 2 < argc; i += 3) {\n"
        "        printf(\"%d\\n\", moy_u_fpu_lazy((uint32_t)strtoul(argv[i], 0, 0),\n"
        "               (uint32_t)strtoul(argv[i + 1], 0, 0),\n"
        "               (uint32_t)strtoul(argv[i + 2], 0, 0)));\n"
        "    }\n    return 0;\n}\n")
    exe = d / "lazy"
    subprocess.run([CC, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(HEADER_DIR),
                    "-o", str(exe), str(src)], check=True)

    def run(*cases):
        args = [str(exe)]
        for insn, ill, mstatus in cases:
            args += [hex(insn), hex(ill), hex(mstatus)]
        out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
        return [int(x) for x in out.split()]
    return run


def test_an_fpu_instruction_with_the_fpu_off_goes_to_the_lazy_enable(lazy):
    cases = [(CSRR_FRM, 0, FS_OFF), (CSRR_FFLAGS, 0, FS_OFF), (CSRR_FCSR, 0, FS_OFF),
             (FLW, 0, FS_OFF), (C_FLWSP, 0, FS_OFF), (FADD, EXT_ILL_FPU, FS_OFF)]
    assert lazy(*cases) == [1] * len(cases)


def test_the_same_instruction_trapping_again_with_the_fpu_off_goes_again(lazy):
    # an interrupt between IDF's enable and the retry turned the FPU off again
    assert lazy(*[(CSRR_FRM, 0, FS_OFF)] * 3) == [1, 1, 1]


def test_a_trap_with_the_fpu_on_is_an_illegal_instruction(lazy):
    cases = [(CSRR_FRM, 0, FS_ON), (FADD, EXT_ILL_FPU, FS_ON), (FLW, 0, FS_DIRTY)]
    assert lazy(*cases) == [0, 0, 0]


def test_what_idf_aborts_on_from_a_task_never_goes(lazy):
    cases = [(HWLP, 0, FS_OFF), (FADD, EXT_ILL_PIE | EXT_ILL_FPU, FS_OFF),
             (CSRR_MCYCLE, 0, FS_OFF), (0, 0, FS_OFF)]
    assert lazy(*cases) == [0, 0, 0, 0]

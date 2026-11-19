"""The P4's batched ESP-NOW send, executed (`native/p4/moy_c6/espnow_shim_proto.h`).

SEND_BATCH is one wire format with two readers: the P4's TX task packs it
(`modmoy_c6.c`, `moyc6_batch_put`) and the C6's shim walks it
(`slave_espnow_shim.c`, `moyc6_batch_next`), both through the inline helpers
in the ONE protocol header the slave build copies. This compiles that header,
unmodified, into a host program and runs a batch through both halves: what
the P4 packs is what the C6 sends, a batch never outgrows the hosted seam,
and a message cut short sends nothing past the cut.

No compiler WARNS locally and FAILS under `CI` (the moy_flush shape).
"""

import os
import shutil
import subprocess
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROTO = ROOT / "native" / "p4" / "moy_c6"

PROGRAM = r"""
#include <assert.h>
#include <stdio.h>
#include "espnow_shim_proto.h"

_Static_assert(MOYC6_BATCH_BYTES <= 8166, "a batch outgrows the hosted seam");
_Static_assert(sizeof(moyc6_batch_t) == 3, "batch header");
_Static_assert(sizeof(moyc6_batch_frame_t) == 9, "frame header");
_Static_assert(sizeof(moyc6_batch_status_t) == 8, "batch status");
_Static_assert(sizeof(moyc6_diag_t) == 30, "diag reply");
_Static_assert(MOYC6_SHIM_VERSION >= MOYC6_SHIM_BATCH, "the shim carries batches");
_Static_assert(MOYC6_SHIM_VERSION < 0x100, "the version rides in an ACK's err");

static uint8_t buf[MOYC6_BATCH_BYTES], data[MOYC6_FRAME_V2];
static const uint8_t mac[6] = {1, 2, 3, 4, 5, 6};

static void roundtrip(void) {
    const uint16_t lens[] = {MOYC6_FRAME_V2, 250, 17, 0, MOYC6_FRAME_V2};
    uint32_t used = 0;
    for (int i = 0; i < 5; i++) {
        memset(data, 0x30 + i, sizeof(data));
        used = moyc6_batch_put(buf, used, i % 2 ? mac : NULL, data, lens[i]);
        assert(used != 0);
    }
    assert(buf[0] == MOYC6_PROTO_VERSION && buf[1] == MOYC6_V_SEND_BATCH && buf[2] == 5);
    uint32_t at = sizeof(moyc6_batch_t);
    for (int i = 0; i < 5; i++) {
        moyc6_batch_frame_t f;
        int32_t off = moyc6_batch_next(buf, used, &at, &f);
        assert(off > 0 && f.len == lens[i]);
        assert(f.dst_valid == (i % 2) && (!f.dst_valid || !memcmp(f.dst, mac, 6)));
        for (uint16_t k = 0; k < f.len; k++) {
            assert(buf[off + k] == 0x30 + i);
        }
    }
    assert(at == used);
    printf("roundtrip ok\n");
}

static void ceilings(void) {
    uint32_t used = 0;
    int n = 0;
    while (moyc6_batch_fits(used ? used : sizeof(moyc6_batch_t), MOYC6_FRAME_V2)) {
        used = moyc6_batch_put(buf, used, NULL, data, MOYC6_FRAME_V2);
        n++;
    }
    assert(moyc6_batch_put(buf, used, NULL, data, MOYC6_FRAME_V2) == 0);
    assert(used <= MOYC6_BATCH_BYTES);
    printf("v2 frames a batch %d\n", n);
    used = 0;
    for (n = 0; n < MOYC6_BATCH_FRAMES; n++) {
        used = moyc6_batch_put(buf, used, NULL, data, 8);
        assert(used != 0);
    }
    assert(moyc6_batch_put(buf, used, NULL, data, 8) == 0 && buf[2] == MOYC6_BATCH_FRAMES);
    printf("frame cap ok\n");
}

static void cut_short(void) {
    uint32_t used = moyc6_batch_put(buf, 0, NULL, data, 100);
    used = moyc6_batch_put(buf, used, mac, data, 200);
    uint32_t at = sizeof(moyc6_batch_t);
    moyc6_batch_frame_t f;
    assert(moyc6_batch_next(buf, used - 1, &at, &f) > 0 && f.len == 100);
    assert(moyc6_batch_next(buf, used - 1, &at, &f) == -1);
    at = sizeof(moyc6_batch_t);
    buf[at + 7] = 0xFF, buf[at + 8] = 0xFF;          // a frame longer than v2's
    assert(moyc6_batch_next(buf, used, &at, &f) == -1);
    printf("cut short ok\n");
}

int main(void) {
    roundtrip();
    ceilings();
    cut_short();
    return 0;
}
"""


def _cc():
    return os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    cc = _cc()
    if cc is None:
        if os.environ.get("CI"):
            pytest.fail("no C compiler: the batch format goes unchecked")
        warnings.warn("no C compiler: the C6 batch format goes unchecked")
        pytest.skip("no C compiler")
    d = tmp_path_factory.mktemp("c6_batch")
    (d / "main.c").write_text(PROGRAM)
    exe = d / "batch"
    subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-g", "-O1",
                    f"-I{PROTO}", str(d / "main.c"), "-o", str(exe)], check=True)
    return subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout


def test_what_the_p4_packs_is_what_the_c6_sends(run):
    assert "roundtrip ok" in run


def test_a_batch_holds_five_v2_frames_or_thirty_two_and_never_more(run):
    assert "v2 frames a batch 5" in run
    assert "frame cap ok" in run


def test_a_batch_cut_short_sends_nothing_past_the_cut(run):
    assert "cut short ok" in run

"""A board's AES-GCM is ESP-IDF's (MBEDTLS_GCM_ALT, on wherever the IDF
takes the chip's AES), and it refuses a NULL input even at length 0, where
mbedTLS's own, which the host and the browser run, takes it. Every sealed
answer ends in an empty record, so a seal that handed it NULL left the tag
unwritten and no answer from a board opened. This harness links moy_seal.c
with the two GCM calls wrapped to refuse a NULL input as the IDF does, and
seals and opens empty records through it."""

import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET = os.path.join(ROOT, "native", "moy_net")

HARNESS = r'''
#include <stdio.h>
#include <string.h>
#include "moy_seal.h"
#include "mbedtls/gcm.h"

#define REFUSE_NULL(in, out) \
    if ((in) == NULL || (out) == NULL) { return MBEDTLS_ERR_GCM_BAD_INPUT; }

int __real_mbedtls_gcm_crypt_and_tag(mbedtls_gcm_context *, int, size_t, const unsigned char *,
                                     size_t, const unsigned char *, size_t,
                                     const unsigned char *, unsigned char *, size_t,
                                     unsigned char *);
int __wrap_mbedtls_gcm_crypt_and_tag(mbedtls_gcm_context *c, int m, size_t n,
                                     const unsigned char *iv, size_t ivn,
                                     const unsigned char *a, size_t an,
                                     const unsigned char *in, unsigned char *out, size_t tn,
                                     unsigned char *tag) {
    REFUSE_NULL(in, out)
    return __real_mbedtls_gcm_crypt_and_tag(c, m, n, iv, ivn, a, an, in, out, tn, tag);
}
int __real_mbedtls_gcm_auth_decrypt(mbedtls_gcm_context *, size_t, const unsigned char *, size_t,
                                    const unsigned char *, size_t, const unsigned char *,
                                    size_t, const unsigned char *, unsigned char *);
int __wrap_mbedtls_gcm_auth_decrypt(mbedtls_gcm_context *c, size_t n, const unsigned char *iv,
                                    size_t ivn, const unsigned char *a, size_t an,
                                    const unsigned char *tag, size_t tn,
                                    const unsigned char *in, unsigned char *out) {
    REFUSE_NULL(in, out)
    return __real_mbedtls_gcm_auth_decrypt(c, n, iv, ivn, a, an, tag, tn, in, out);
}

int main(void) {
    uint8_t kb[MOY_SEAL_KEY] = {7}, head[MOY_SEAL_HEAD], tag[MOY_SEAL_TAG];
    memset(tag, 0xAA, sizeof tag);
    int s = moy_seal_rec(1, kb, 3, 1, 200, 1, head, NULL, 0, tag);
    int o = moy_seal_rec(0, kb, 3, 1, 200, 1, head, NULL, 0, tag);
    uint8_t wtag[MOY_SEAL_TAG];
    int ws = moy_seal_ws(1, kb, 2, 0, 9, NULL, 0, wtag);
    int wo = moy_seal_ws(0, kb, 2, 0, 9, NULL, 0, wtag);
    printf("%d %d %d %d\n", s, o, ws, wo);
    return 0;
}
'''


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("gcc")
    if cc is None:
        pytest.skip("no C compiler")
    d = tmp_path_factory.mktemp("seal_gcm")
    src = d / "main.c"
    src.write_text(HARNESS)
    lib = os.path.join(NET, "mbedtls", "library")
    exe = d / "seal_gcm"
    args = [cc, "-O1", "-o", str(exe), str(src), os.path.join(NET, "moy_seal.c"),
            "-I", NET, "-I", os.path.join(NET, "mbedtls", "include"),
            "-I", os.path.join(os.path.dirname(NET), "moy_kernel"),
            "-DMBEDTLS_CONFIG_FILE=\"moy_seal_mbedtls.h\"",
            "-Wl,--wrap=mbedtls_gcm_crypt_and_tag", "-Wl,--wrap=mbedtls_gcm_auth_decrypt"]
    args += sorted(os.path.join(lib, f) for f in os.listdir(lib) if f.endswith(".c"))
    r = subprocess.run(args, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    return exe


def test_an_empty_record_seals_and_opens_under_a_gcm_that_refuses_null(harness):
    out = subprocess.run([str(harness)], capture_output=True, text=True, check=True).stdout
    assert out.split() == ["0", "0", "0", "0"], out

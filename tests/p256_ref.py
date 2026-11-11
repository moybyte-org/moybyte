"""P-256, HKDF and the console password's record in plain Python, written
from the texts (SEC 1, RFC 5869, RFC 8018, RFC 9383 and native/moy_net/
moy_seal.h's THE PASSWORD) alone, so a mistake moy_seal.c makes fails here
instead of agreeing with itself. tests/test_web_signin.py's second page runs
SPAKE2+ on it; tests/test_ssh_service.py holds the record to it.
"""

import hashlib
import hmac

P = 2 ** 256 - 2 ** 224 + 2 ** 192 + 2 ** 96 - 1
CB = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
     0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)
# RFC 9382 section 6 (and RFC 9383 section 4), P-256's M and N, compressed as
# the RFCs print them.
RFC_M = "02886e2f97ace46e55ba9dd7242579f2993b64e16ef3dcab95afd497333d8fa12f"
RFC_N = "03d8bbd6c639c62937b04d997f38c3770719c629d7014d49a24b4f98baa1292b49"

PW_ITER = 1 << 15                       # moy_seal.h's MOY_SEAL_PW_ITER
CONTEXT = b"moy2 SPAKE2+-P256-SHA256-HKDF-SHA256-HMAC-SHA256"


def ec_add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and (a[1] + b[1]) % P == 0:
        return None
    if a == b:
        m = (3 * a[0] * a[0] - 3) * pow(2 * a[1], -1, P)
    else:
        m = (b[1] - a[1]) * pow(b[0] - a[0], -1, P)
    x = (m * m - a[0] - b[0]) % P
    return x, (m * (a[0] - x) - a[1]) % P


def ec_mul(k, pt):
    out = None
    while k:
        if k & 1:
            out = ec_add(out, pt)
        pt = ec_add(pt, pt)
        k >>= 1
    return out


def ec_neg(pt):
    return pt[0], (-pt[1]) % P


def ec_dec(raw):
    if raw[0] == 4:
        return int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:], "big")
    x = int.from_bytes(raw[1:], "big")
    y = pow((x ** 3 - 3 * x + CB) % P, (P + 1) // 4, P)
    return x, y if y % 2 == raw[0] % 2 else P - y


def ec_enc(pt):
    return b"\x04" + pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")


M = ec_dec(bytes.fromhex(RFC_M))
NP = ec_dec(bytes.fromhex(RFC_N))


def hm(key, *parts):
    return hmac.new(key, b"".join(parts), hashlib.sha256).digest()


def hkdf(salt, ikm, info, n):
    prk = hm(salt or bytes(32), ikm)
    out, t, i = b"", b"", 1
    while len(out) < n:
        t = hm(prk, t, info, bytes([i]))
        out += t
        i += 1
    return out[:n]


def pw_scalars(password, salt, iterations=PW_ITER):
    """(w0, w1): PBKDF2-HMAC-SHA256's 32 bytes widened by HKDF to two 40-byte
    halves, each mod the order, a zero read as 1."""
    k = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations, 32)
    wide = hkdf(None, k, b"moy2 spake2+ w0 w1", 80)
    return (int.from_bytes(wide[:40], "big") % N or 1,
            int.from_bytes(wide[40:], "big") % N or 1)


def record(password, salt, iterations=PW_ITER):
    w0, w1 = pw_scalars(password, salt, iterations)
    return "$moy2$%d$%s$%s$%s" % (iterations, salt, w0.to_bytes(32, "big").hex(),
                                  ec_enc(ec_mul(w1, G)).hex())


def lp(b):
    return len(b).to_bytes(8, "little") + b


def spake2p_keys(context, idp, idv, x_share, y_share, z, v, w0):
    """RFC 9383 section 3.4: (K_shared, confirmP, confirmV)."""
    tt = (lp(context) + lp(idp) + lp(idv) + lp(ec_enc(M)) + lp(ec_enc(NP)) + lp(x_share)
          + lp(y_share) + lp(ec_enc(z)) + lp(ec_enc(v)) + lp(w0.to_bytes(32, "big")))
    main = hashlib.sha256(tt).digest()
    kc = hkdf(None, main, b"ConfirmationKeys", 64)
    return hkdf(None, main, b"SharedKey", 32), hm(kc[:32], y_share), hm(kc[32:], x_share)

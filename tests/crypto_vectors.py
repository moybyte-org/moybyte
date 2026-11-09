"""The crypto rows' known answers (native/moy_net/moy_crypto.h), one place for
every tier's test: FIPS 180-2's SHA-256 of "abc", RFC 4231's HMAC-SHA256
cases 1, 2 and 6, the GCM spec's (McGrew and Viega) test cases 3 and 15 with
no AAD and the nonce first, RFC 7748 section 6.1's X25519 keys. Each was
checked against pyca's cryptography when written."""

SHA256_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
SHA256_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
HMAC = (
    (b"\x0b" * 20, b"Hi There",
     "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"),
    (b"Jefe", b"what do ya want for nothing?",
     "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"),
    (b"\xaa" * 131, b"Test Using Larger Than Block-Size Key - Hash Key First",
     "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54"),
)
GCM_NONCE = "cafebabefacedbaddecaf888"
GCM_PLAIN = ("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
             "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b391aafd255")
GCM = (
    ("feffe9928665731c6d6a8f9467308308",
     "42831ec2217774244b7221b784d0d49ce3aa212f2c02a4e035c17e2329aca12e"
     "21d514b25466931c7d8f6a5aac84aa051ba30b396a0aac973d58e091473f5985"
     "4d5c2af327cd64a62cf35abd2ba6fab4"),
    ("feffe9928665731c6d6a8f9467308308feffe9928665731c6d6a8f9467308308",
     "522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa"
     "8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f662898015ad"
     "b094dac5d93471bdec1a502270e3cc6c"),
)
X25519_ALICE = "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a"
X25519_ALICE_PUB = "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
X25519_BOB = "5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb"
X25519_BOB_PUB = "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
X25519_SHARED = "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742"

# What tests/fixtures/elf/elf_crypto.moy prints, natively and as WASI: each
# answer (its first 8 bytes), and the errno of each mistake.
PROGRAM = [
    "random 16", "random.max -1 EINVAL", "random.short -1 ERANGE",
    "sha256 " + SHA256_ABC[:16], "sha256.short -1 ERANGE",
    "hmac_sha256 " + HMAC[1][2][:16],
    "aes_gcm_open 64", "aes_gcm_open.same 1", "aes_gcm_open.key -1 EINVAL",
    "aes_gcm_open.changed -1 EBADMSG",
    "aes_gcm_seal 35", "aes_gcm_seal.opened moybyte", "aes_gcm_seal.short -1 ERANGE",
    "x25519.public " + X25519_ALICE_PUB[:16], "x25519.shared " + X25519_SHARED[:16],
    "x25519.small -1 EINVAL", "x25519.secret -1 EINVAL",
]

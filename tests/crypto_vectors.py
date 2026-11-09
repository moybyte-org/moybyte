"""The crypto rows' known answers (native/moy_net/moy_crypto.h), one place for
every tier's test: FIPS 180-2's SHA-256 of "abc", RFC 4231's HMAC-SHA256
cases 1, 2 and 6, the GCM spec's (McGrew and Viega) test cases 3 and 15 with
no AAD and the nonce first, RFC 7748 section 6.1's X25519 keys, RFC 8032
section 7.1's Ed25519 TEST 1, 2, 3 and SHA(abc). Each was checked against
pyca's cryptography when written."""

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
# RFC 8032 section 7.1: (secret, public key, message, signature).
ED25519 = (
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
     "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
     "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
     "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
    ("833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
     "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
     "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
     "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
     "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b589"
     "09351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704"),
)

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
    "ed25519.public " + ED25519[2][1][:16], "ed25519.sign " + ED25519[2][3][:16],
    "ed25519.sign.same 1", "ed25519.verify 1", "ed25519.verify.changed 0",
    "ed25519.verify.key 0", "ed25519.verify.sig -1 EINVAL", "ed25519.sign.short -1 ERANGE",
    "ed25519.secret -1 EINVAL",
]

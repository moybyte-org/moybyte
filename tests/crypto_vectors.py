"""The crypto rows' known answers (native/moy_net/moy_crypto.h), one place for
every tier's test: FIPS 180-2's SHA-256 of "abc", RFC 4231's HMAC-SHA256
cases 1, 2 and 6, the GCM spec's (McGrew and Viega) test cases 3 and 15 with
no AAD and the nonce first, NIST SP 800-38A F.5.1 and RFC 3686's AES-CTR
vectors 2 and 7, NIST CAVP's CCM decryption-verification vectors (DVPT128 and
DVPT256, 13-byte nonce, no associated data), Meshtastic's own PKI packet
(its firmware's test/test_crypto: X25519, SHA-256 of the shared secret, AES-CCM
with an 8-byte tag), RFC 7748 section 6.1's X25519 keys, RFC 8032 section
7.1's Ed25519 TEST 1, 2, 3 and SHA(abc). Each was checked against pyca's
cryptography when written."""

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
# AES-CTR: (key, the initial counter block, plaintext, ciphertext).
CTR = (
    ("2b7e151628aed2a6abf7158809cf4f3c", "f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff",
     "6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51"
     "30c81c46a35ce411e5fbc1191a0a52eff69f2445df4f9b17ad2b417be66c3710",
     "874d6191b620e3261bef6864990db6ce9806f66b7970fdff8617187bb9fffdff"
     "5ae4df3edbd5d35e5b4f09020db03eab1e031dda2fbe03d1792170a0f3009cee"),
    ("ae6852f8121067cc4bf7a5765577f39e", "00000030000000000000000000000001",
     b"Single block msg".hex(), "e4095d4fb7a7b3792d6175a3261311b8"),
    ("776beff2851db06f4c8a0542c8696f6c6a81af1eec96b4d37fc1d689e6c1c104",
     "00000060db5672c97aa8f0b200000001",
     b"Single block msg".hex(), "145ad01dbf824ec7560863dc71e3e0c0"),
)
# AES-CCM, no associated data: (key, 13-byte nonce, tag length, plaintext,
# the ciphertext then the tag).
CCM = (
    ("197afb02ffbd8f699dacae87094d5243", "5a8aa485c316e9403aff859fbb", 4,
     "a16a2e741f1cd9717285b6d882c1fc53655e9773761ad697",
     "4a550134f94455979ec4bf89ad2bd80d25a77ae94e456134a3e138b9"),
    ("90929a4b0ac65b350ad1591611fe4829", "5a8aa485c316e9403aff859fbb", 16,
     "a16a2e741f1cd9717285b6d882c1fc53655e9773761ad697",
     "4bfe4e35784f0a65b545477e5e2f4bae0e1e6fa717eaf2cb6a9a970b9beb2ac1"
     "bd4fd62168f8378a"),
    ("1b0e8df63c57f05d9ac457575ea764524b8610ae5164e6215f426f5a7ae6ede4",
     "a544218dadd3c10583db49cf39", 16,
     "3c0e2815d37d844f7ac240ba9d6e3a0b2a86f706e885959e",
     "f0050ad16392021a3f40207bed3521fb1e9f808f49830c423a578d179902f912"
     "f9ea1afbce1120b3"),
    ("4bb3c4a4f893ad8c9bdc833c325d62b3", "5a8aa485c316e9403aff859fbb", 4, "", "90156f3f"),
)
# Meshtastic's PKI test packet: the receiver's X25519 secret, the sender's
# public key, the 16-byte header and payload as the radio carried it (the
# ciphertext, the 8-byte tag, the 4-byte extra nonce), the AES-CCM key
# (SHA-256 of the shared secret), the 13-byte nonce (the packet id, the extra
# nonce, the sender, little-endian) and the Data protobuf inside.
MESH_PKI_SECRET = "a00330633e63522f8a4d81ec6d9d1e6617f6c8ffd3a4c698229537d44e522277"
MESH_PKI_PEER = "db18fc50eea47f00251cb784819a3cf5fc361882597f589f0d7ff820e8064457"
MESH_PKI_RADIO = ("8c646d7a2909000062d6b2136b000000"
                  "40df24abfcc30a17a3d9046726099e796a1c036a792b")
MESH_PKI_KEY = "777b1545c9d6f9a258ee0fd71153c78a50c6bb56c1f4fff2ea0ef28094de0186"
MESH_PKI_NONCE = "62d6b213036a792b2909000000"
MESH_PKI_PLAIN = "08011204746573744800"
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
    "aes_ctr " + CTR[1][3][:16], "aes_ctr.block -1 EINVAL", "aes_ctr.short -1 ERANGE",
    "aes_ccm_open " + MESH_PKI_PLAIN[:16], "aes_ccm_seal " + MESH_PKI_RADIO[32:48],
    "aes_ccm_seal.same 1", "aes_ccm_open.changed -1 EBADMSG", "aes_ccm.tag -1 EINVAL",
]

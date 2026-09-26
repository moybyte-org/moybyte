// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Nikola Jovicic
//
// The provenance key a module must carry to load on this board.
//
// A per-architecture module is native code, so the sandbox is whatever the
// compiler emitted (docs/wasm_tier_plan_2026-09.md): a board runs a module
// only if it says which wasm it came from, which fork of the runtime it was
// compiled for, and with which compiler flags. The key is the content of the
// custom section MOY_WASM_KEY_SECTION, one "name value" field per line:
//
//     moybyte-aot 1
//     wasm <sha256 of the canonical .wasm, 64 hex>
//     fork <the fork commit, 40 hex>
//     <this chip's target block, below>
//
// Everything after the wasm line must equal "fork " MOY_WASM_FORK_COMMIT
// "\n" followed by the chip's block, byte for byte. The wasm hash is recorded
// and reported; nothing on the board can recompute it.
//
// THIS FILE IS THE ONE STATEMENT OF THE FLAGS. tools/wasm_module.py parses the
// blocks below and derives the wamrc command line from them, so a module the
// tool builds carries exactly the key this check wants. Keep each block to
// one string literal per line.

#ifndef MOY_WASM_KEY_H
#define MOY_WASM_KEY_H

#include "wamr_pin.h"

#define MOY_WASM_KEY_SECTION "moybyte.key"
#define MOY_WASM_KEY_MAGIC "moybyte-aot 1\n"

// The signature. A module FILE is the module, then its signature, then the
// signature's length in bytes (4, little-endian), then MOY_WASM_SIG_MAGIC:
//
//     <module> <signature> <length> "moybyte-sig1"
//
// The signature is the OTA scheme's -- RSA, PKCS#1 v1.5, SHA-256, checked by
// moy_ota.verify_sig against the keys in moy_ota.OTA_PUBLIC_KEYS -- over the
// text
//
//     MOY_WASM_SIG_SCHEME "\n" <chip> "\n" <module length> "\n" <sha256 hex>
//
// so it covers every byte of the module, the key section included. A board
// strips the trailer and loads the module only after the signature verifies.
#define MOY_WASM_SIG_MAGIC "moybyte-sig1"
#define MOY_WASM_SIG_SCHEME "moybyte-aot-sig 1"

// ESP32-S3: Xtensa LX7. --size-level=0 is the large code model, which keeps
// constants out of a literal pool: the S3 fetches AOT text through the
// instruction-bus alias, and a load through that alias faults. The LX7 takes a
// misaligned load or store in hardware, which the fork's compiler knows from
// the cpu (native/moy_wasm/README.md, "Misaligned access").
#define MOY_WASM_KEY_ESP32S3 \
    "target xtensa\n" \
    "cpu esp32s3\n" \
    "abi -\n" \
    "features -\n" \
    "opt 3\n" \
    "size 0\n" \
    "bounds 1\n" \
    "stack-bounds 1\n" \
    "xip 0\n"

// ESP32-P4: RV32IMAFC, hard-float single ABI. Without +m LLVM emits
// __umodsi3 and friends, which WAMR's RISC-V symbol table does not carry.
// +unaligned-scalar-mem because the P4 takes a misaligned load or store in
// hardware: without it every access whose alignment the compiler cannot see
// is split into bytes (native/moy_wasm/README.md, "Misaligned access").
#define MOY_WASM_KEY_ESP32P4 \
    "target riscv32\n" \
    "cpu generic-rv32\n" \
    "abi ilp32f\n" \
    "features +m,+a,+f,+c,+unaligned-scalar-mem\n" \
    "opt 3\n" \
    "size 3\n" \
    "bounds 1\n" \
    "stack-bounds 1\n" \
    "xip 0\n"

#endif

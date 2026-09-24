#!/usr/bin/env bash
# Build a wamrc that can target Xtensa (ESP32-S3). The prebuilt wamrc in
# WAMR's releases is linked against an LLVM WITHOUT the Xtensa backend
# ("llvm get target from triple (xtensa-pc-linux-gnu) failed", measured
# 2026-09-24), and Espressif's esp-clang tarballs ship no LLVM dev libraries
# or cmake config to link wamrc against. So: the same thing WAMR's own
# build-scripts/build_llvm.py --platform xtensa does -- Espressif's LLVM fork
# at the branch WAMR pins, X86 + experimental Xtensa backends, static libs,
# no tools -- then wamr-compiler against it.
#
# Cost: ~45 min on 12 cores, ~3 GB of disk under wamr/core/deps/llvm.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
LLVM_DIR="${HERE}/wamr/core/deps/llvm"
JOBS="${JOBS:-10}"

if [ ! -d "${LLVM_DIR}/llvm" ]; then
  echo "== cloning espressif/llvm-project xtensa_release_18.1.2 (sparse: llvm, cmake, third-party)"
  mkdir -p "${HERE}/wamr/core/deps"
  git clone --depth 1 --branch xtensa_release_18.1.2 --filter=blob:none --sparse \
    https://github.com/espressif/llvm-project.git "${LLVM_DIR}"
  (cd "${LLVM_DIR}" && git sparse-checkout set llvm cmake third-party)
fi

mkdir -p "${LLVM_DIR}/build"
cd "${LLVM_DIR}/build"
if [ ! -f build.ninja ]; then
  echo "== configuring LLVM"
  cmake -G Ninja ../llvm \
    -DCMAKE_BUILD_TYPE=Release \
    -DLLVM_TARGETS_TO_BUILD="X86" \
    -DLLVM_EXPERIMENTAL_TARGETS_TO_BUILD="Xtensa" \
    -DLLVM_APPEND_VC_REV=OFF \
    -DLLVM_BUILD_EXAMPLES=OFF -DLLVM_BUILD_LLVM_DYLIB=OFF \
    -DLLVM_ENABLE_BINDINGS=OFF -DLLVM_ENABLE_IDE=OFF \
    -DLLVM_ENABLE_LIBEDIT=OFF -DLLVM_ENABLE_TERMINFO=OFF \
    -DLLVM_ENABLE_ZLIB=OFF -DLLVM_ENABLE_ZSTD=OFF -DLLVM_ENABLE_LIBXML2=OFF \
    -DLLVM_INCLUDE_BENCHMARKS=OFF -DLLVM_INCLUDE_DOCS=OFF \
    -DLLVM_INCLUDE_EXAMPLES=OFF -DLLVM_INCLUDE_UTILS=OFF \
    -DLLVM_INCLUDE_TESTS=OFF -DLLVM_INCLUDE_TOOLS=OFF \
    -DLLVM_OPTIMIZED_TABLEGEN=ON -DLLVM_PARALLEL_LINK_JOBS=4
fi
echo "== building LLVM (-j${JOBS})"
ninja -j"${JOBS}"

echo "== building wamrc"
cd "${HERE}/wamr/wamr-compiler"
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j"${JOBS}"
ls -la "${HERE}/wamr/wamr-compiler/build/wamrc"
"${HERE}/wamr/wamr-compiler/build/wamrc" --version || true
echo "== done"

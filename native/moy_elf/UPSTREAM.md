# elf_loader — vendored, patched

`elf_loader/` is Espressif's [elf_loader](https://components.espressif.com/components/espressif/elf_loader)
component (a folder of esp-iot-solution) at a pinned release, with Moybyte's
fixes applied from `patches/`. It is Apache-2.0 (`elf_loader/license.txt`).

**The release, the SHA-256 of the registry archive it is fetched as, the
esp-iot-solution commit that archive names, the patch's hash and the hash of
every copied file are in [`elf_loader_vendor.json`](elf_loader_vendor.json)**,
written by `make vendor-elf-loader` and checked by
`tests/test_elf_loader_vendor.py`.

## What is copied

`tools/vendor_elf_loader.py`'s `FILES`: the loader, its relocations for
Xtensa (the S3s) and RISC-V (the P4s), its validation and the headers they
include. Not its shared-object path, its symbol tables or its CMake and
Kconfig: `micropython.cmake` sets the switches its Kconfig would.

## What the patch fixes

`patches/moybyte.patch`'s header says each fix and why: `.text`'s size kept
exact, an `R_XTENSA_RELATIVE` relocation to one past a section's end mapped
instead of becoming 0, the validation's section-name table taken from the
loader's own allocator (PSRAM) instead of internal RAM, and two messages that
formatted a long long, which the boards' printf cannot.

The fixes stay in our copy; none is reported upstream.

## Changing the loader

Never edit `elf_loader/`: the next re-vendor reverts it, and the test fails
first. A change is a change to `patches/moybyte.patch` (a diff over the
release's tree), then `make vendor-elf-loader`.

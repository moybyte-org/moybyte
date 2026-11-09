# The SDK's libm (RISC-V)

What a P4 module's `<math.h>` links: the P4's libmoyos compiles these
(`tools/elf_cart.py`, the chip's `"libm"`), because the RISC-V toolchain's
libm addresses its constants absolutely and no module can relocate that
(`sdk/README.md`, "The C library"). The S3 and the host link their
toolchain's libm.

- `libm/` is MicroPython's `lib/libm` (fdlibm's float functions by way of
  newlib-nano, each file carrying Sun's notice, and `math.c` under the MIT
  licence), and `libm_dbl/` its `lib/libm_dbl` (musl 1.1.16's double
  functions, MIT, `libm_dbl/README`), both as MicroPython v1.28.0 has them,
  file for file. Left out: the Thumb assembly (`thumb_vfp_sqrt*.c`), and
  `sf_sin.c`, `sf_cos.c` and `sf_tan.c`, whose float reduction loses the
  low bits of a large argument.
- `trigf.c` is `sinf`, `cosf` and `tanf` over the same float kernels, reduced
  in float up to 2^7*pi/2 and by `libm_dbl/`'s double reduction beyond.

None of it sets errno or writes a global. `tests/test_sdk_libm.py` checks
that, the copy against a MicroPython tree when one is fetched, and each
function against glibc's.

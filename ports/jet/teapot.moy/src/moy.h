// The console's imports this cart uses: module "moy", each at its row's type in
// moy-spec's proposals/wasm-imports.json. Nothing else is imported.
#pragma once

#include <stdint.h>

#define MOY_IMPORT(name) __attribute__((import_module("moy"), import_name(name)))

#ifdef __cplusplus
extern "C" {
#endif

// The frame: 320 x 240 RGB565 words, little-endian, row-major. Only inside
// _draw, at most once per _draw; verbs called after it draw over it.
MOY_IMPORT("blit565") void moy_blit565(const uint16_t *frame);

MOY_IMPORT("rect") void moy_rect(int32_t x, int32_t y, int32_t w, int32_t h, int32_t c);
MOY_IMPORT("print") void moy_print(const char *s, int32_t len, int32_t x, int32_t y,
                                   int32_t c);

// btn(b, player): left 0, right 1, up 2, down 3, a 4, b 5.
MOY_IMPORT("btn") int32_t moy_btn(int32_t b, int32_t player);

// Milliseconds since the cart started.
MOY_IMPORT("time") int32_t moy_time(void);

// config.json's value for `key` as text (a boolean reads "1" or "0"); its
// whole length, or -1 when the key is absent.
MOY_IMPORT("cfg") int32_t moy_cfg(const char *key, int32_t key_len, char *dst,
                                  int32_t dst_len);

// Up to `len` bytes of the cart's own file `name` from `offset`; with `len` 0,
// how many bytes remain from `offset`.
MOY_IMPORT("read") int32_t moy_read(const char *name, int32_t name_len, int32_t offset,
                                    void *dst, int32_t len);

MOY_IMPORT("pmem") int32_t moy_pmem(int32_t slot, int32_t v, int32_t write);

#ifdef __cplusplus
}
#endif

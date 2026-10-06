// A cart as it travels (docs/kernel_store_2026-10.md section 8; #127, #122):
// the `.moy` archive, the wire's skip rule, and adopt, which moves a staged
// cart into its place on the shelf.
//
// The archive is firmware/web_runner/moy_store.mjs's codec, byte for byte on
// the writing side: STORED entries named `<folder>/<path>`, a fixed
// 1980-01-01 date, the central directory and its end record; files in name
// order, at most six folders deep. A reader takes stored and deflated entries
// (the image's uzlib), sizes from the central directory, strips the one top
// folder every entry sits under, and refuses a path that climbs, is absolute,
// or names what the wire skips. runtime/moy_carts.py's pack/unpack/adopt are
// the twins; tests/test_store_native.py pins the archive against the
// browser's zipStore and unzip both ways.

#ifndef MOY_PACK_H
#define MOY_PACK_H

#include <stddef.h>
#include <stdint.h>

#include "moy_fs.h"

// `_skip`: what never crosses the wire or goes into an archive -- journal/,
// thumbs/, __pycache__/, journal.jsonl, and moy_fs's .bak and .tmp. With
// `history`, journal/ and journal.jsonl are let through.
int moy_store_skip(const char *name, size_t n, int history);

// The cart folder at `cart` written to `dest` as an archive whose entries sit
// under `folder`: the number of files, or a negative errno value.
int moy_pack(const char *cart, const char *folder, const char *dest, int history);

// The archive at `archive` unpacked into the folder `dest` (made): the number
// of files, or a negative errno value (EINVAL: no archive). `top` gets the
// folder its entries sat under ("" for none), cut at `cap`.
int moy_unpack(const char *archive, const char *dest, char *top, size_t cap);

// The staged cart folder `stage` moved to `target` by one rename; a cart
// already there goes aside to `<stage>.old` first and is removed after, so a
// cut leaves the old cart in place or aside, never half of either.
int moy_adopt(const char *stage, const char *target);

#endif // MOY_PACK_H

// moy_str: the strings C draws, as data (docs/kernel_toolkit_2026-10.md §2.3).
//
// THE TABLE is strings/en.json, one entry per dotted id, English complete,
// generated into moy_str_table.c by tools/gen_ui_tables.py and committed
// (tests/test_ui_tables.py fails a stale one). It lives in the spine because
// every image links the spine: the kernel's floor draws these words on an
// image with no toolkit (the Zero denies native/moy_ui), and moy_chrome, the
// recovery screen and the toolkit read the one table.
//
// A LANGUAGE PACK overlays it entry by entry (moy_str_set). A pack is
// untrusted data: an entry whose conversions (%s, %u, %d, %08x, in order)
// differ from English's is refused, and that id keeps reading English, so a
// table format handed to snprintf always takes the arguments its caller
// passes. A pack's text is copied into the spine's own allocation (PSRAM on a
// board), never borrowed from a VM's heap: the floor reads it with no VM.
// The pack is written on the VM's task between frames.
//
// The serial protocol's words (the recovery choices kernel_gate.py parses)
// read moy_str_en, never the pack.

#ifndef MOY_STR_H
#define MOY_STR_H

#include <stdarg.h>
#include <stddef.h>

#include "moy_str_table.h"

enum { MOY_STR_OK = 0, MOY_STR_REFUSED = 1, MOY_STR_BAD = 2, MOY_STR_NOMEM = 3 };

// The id's text: its pack entry, else English; "" for an id out of range.
const char *moy_str(int id);
// The id named `name` (n bytes), or -1.
int moy_str_id(const char *name, size_t n);
// Install the pack's entry for `id` (n bytes of UTF-8); a NULL text removes
// it. REFUSED when its conversions differ from English's (the id reads
// English), BAD for an id out of range, NOMEM when the copy cannot be held.
int moy_str_set(int id, const char *text, size_t n);
// Remove every pack entry: the table reads English.
void moy_str_reset(void);
// snprintf over the id's text as the format (its conversions are English's).
int moy_str_fmt(char *out, size_t n, int id, ...);
// The id's text copied into `out` (cut at n - 1 bytes, never mid-codepoint).
int moy_str_copy(char *out, size_t n, int id);

#endif // MOY_STR_H

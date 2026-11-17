// The core carts and the removal record: which built-ins a console keeps, and
// the optional ones it removed (runtime/carts/moy_seed.py's comment above
// CORE is the rule's prose).
//
// CORE is moy_core.h, generated from moy_seed.CORE (tools/gen_core_carts.py).
// THE RECORD is `system/removed` beside the carts folder `root`
// (moy_store_base._sibling_path's rule), one cart folder a line; a line that
// names no optional built-in, and a repeated line, is not read, and every
// write rewrites the record as it was read, crash-safe (moy_fs_publish).
// THE BROWSER CONSOLE is one line, MOY_REMOVED_ADDON: its add-on is a folder
// per page (MOY_REMOVED_ADDON_CART, then the page's 12 hex: moy_page.h's
// MOY_PAGE_CART), and a removal of any of them records the console, which a
// board then never fetches again (runtime/carts/web_addon.py's AddonStep);
// installing any of them drops the line.
// Every removal writes it, before the cart goes: the shell's (runtime/shell/
// pkg.py's removed, through moy_seed.note_removed on every tier), the C
// shell's `pkg remove` (native/moy_sh/moy_sh_store.c) and a page's delete
// over the sync wire (native/moy_net/moy_sync_apply.c's dc); an install
// drops the line (pkg.landed, and a cart folder the wire brings back).

#ifndef MOY_REMOVED_H
#define MOY_REMOVED_H

#include "moy_fs.h"

#define MOY_REMOVED_NAME "system/removed"
#define MOY_REMOVED_ADDON "web_console"
#define MOY_REMOVED_ADDON_CART "web_console_"

// Whether the cart `name` (an id, or a folder `<id>.moy`) is a core cart.
int moy_removed_core(const char *name);

// Whether the built-in `name` may be removed: one of the built-ins'
// namespace (MOY_BUILTIN_NS) that is not core.
int moy_removed_removable(const char *name);

// Whether a removal of `name` (an id or a folder) is recorded: an optional
// built-in, or a browser console add-on.
int moy_removed_recorded(const char *name);

// The record's folders, one a line, each ending in '\n': 0 and `out`
// (moy_buf_free it; empty when the store holds no record or an unreadable
// one), or MOY_ENOMEM.
int moy_removed_load(const char *root, moy_buf_t *out);

// `folder` recorded (an add-on's as MOY_REMOVED_ADDON): 0, MOY_FS_NONE when
// a removal of it records nothing or the record holds it already, or an
// errno value.
int moy_removed_note(const char *root, const char *folder);

// `folder`'s line (an add-on's MOY_REMOVED_ADDON) taken off the record: 0, MOY_FS_NONE when the record does
// not hold it, or an errno value.
int moy_removed_drop(const char *root, const char *folder);

#endif // MOY_REMOVED_H

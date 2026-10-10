// The app ABI's kernel half (docs/kernel_appabi_2026-10.md): the grants, the
// grant policy, and the roles served in C. roles.json beside this file is the
// role table; a C row there is a function here, and tests/test_moy_app.py holds
// the two equal.
//
// A moy_appabi_t is the state the C rows read: the grant table (kind GRANT, rows
// keyed by the cart's id), the settings rows prefs writes into, the damage
// flags and the clipboard. On a board it is the kernel's (moy_app_kernel: made
// once, never freed, so a VM stop leaves it and a return start reads it, its
// rows moy_spine_kernel's); elsewhere each console owns one.
//
// A grant is a row: the app's id, its class, its role mask, its files kind,
// its prefs namespace and an OWNER handle it names. A SHIPPED grant is the
// console's own app's and is idempotent by id: a start registering the app
// again gets the row it had, so a stop leaves the count where it was. A RUN
// grant is a user app's run's and ends with it (moy_app_end), or at the next
// fresh start. Every role verb takes the grant first and answers DENIED for a
// role it does not hold, STALE for a grant that ended.
//
// The policy is C's: the permission each role is granted by, the files kinds,
// the manifest refusal and the key a cart's grant is made under.
//
// Every byte comes from the moy_htab_mem_t the state is made with.

#ifndef MOY_APP_H
#define MOY_APP_H

#include <stddef.h>
#include <stdint.h>

#include "moy_htab.h"
#include "moy_settings.h"

enum {
    MOY_APP_OK = 0,
    MOY_APP_STALE = 1,      // the grant ended
    MOY_APP_FULL = 2,
    MOY_APP_NOMEM = 3,
    MOY_APP_DENIED = 4,     // the grant does not hold this role
    MOY_APP_NOSTORE = 5,    // no writable store: NO_STORE
    MOY_APP_IO = 6,         // the store failed
    MOY_APP_BAD = 7,        // a refused argument: an unknown kind or token, a name too long
    MOY_APP_ABSENT = 8,     // no such cart, app, key or document, or a build without it
    MOY_APP_NEEDS_VM = 9,   // a shell-served verb while no VM runs
};

// The roles, in roles.json's order: a grant's mask is 1 << role.
enum {
    MOY_ROLE_DAMAGE = 0, MOY_ROLE_SURFACE, MOY_ROLE_THEME, MOY_ROLE_FILES,
    MOY_ROLE_CARTS, MOY_ROLE_NAV, MOY_ROLE_PREFS, MOY_ROLE_NOTIFY,
    MOY_ROLE_WALLPAPER, MOY_ROLE_ARTWORK, MOY_ROLE_CLIPBOARD, MOY_ROLE_INSTALL,
    MOY_ROLE_N
};

// The C rows, in roles.json's order: what moy_app_count counts.
enum {
    MOY_ROW_DAMAGE_ALL = 0, MOY_ROW_DAMAGE_AGAIN,
    MOY_ROW_PREFS_GET, MOY_ROW_PREFS_SET, MOY_ROW_PREFS_CLEAR,
    MOY_ROW_CLIPBOARD_PUT_TEXT, MOY_ROW_CLIPBOARD_TEXT, MOY_ROW_CLIPBOARD_KIND,
    MOY_ROW_CLIPBOARD_SEQ,
    MOY_ROW_N
};

#define MOY_GRANT_SLOTS 32u       // grants a table holds
#define MOY_APP_ID_MAX 63u      // an id's, and a prefs namespace's, bytes
#define MOY_APP_KEY_MAX 127u    // a namespaced prefs key's bytes
#define MOY_APP_CLIP_MAX 4096u  // the clipboard's text, in bytes (configuration)
#define MOY_APP_KINDS 4u        // the files kinds a permission may name

enum { MOY_GRANT_SHIPPED = 0, MOY_GRANT_RUN = 1 };
enum { MOY_DAMAGE_ALL = 1u, MOY_DAMAGE_AGAIN = 2u };
enum { MOY_CLIP_EMPTY = 0, MOY_CLIP_TEXT = 1 };

typedef struct moy_app moy_appabi_t;

typedef struct {
    uint8_t id_len, ns_len, cls;
    int8_t kind;                // a files kind, 0 .. MOY_APP_KINDS - 1, or -1
    uint32_t roles;
    uint32_t owner;             // an OWNER handle the grant names, or 0
    char id[MOY_APP_ID_MAX + 1];
    char ns[MOY_APP_ID_MAX + 1];
} moy_grant_t;

// A console's own state over `prefs` (NULL: prefs answer ABSENT), or NULL.
moy_appabi_t *moy_app_new(const moy_htab_mem_t *mem, moy_settings_t *prefs);
void moy_app_free(moy_appabi_t *a);                // NULL is a no-op
// The kernel's: made at the first call that passes `mem`, over
// moy_spine_kernel's settings rows. NULL `mem` answers it only once made.
moy_appabi_t *moy_app_kernel(const moy_htab_mem_t *mem);
// What a fresh start puts back: every RUN grant ended, damage and the
// clipboard cleared. SHIPPED grants stay for their apps' next registration.
void moy_app_fresh(moy_appabi_t *a);

// -- the grants -----------------------------------------------------------------

// Grant `roles` to `id` as `cls`: OK and the handle, BAD (an empty or long id
// or namespace, a kind out of range), FULL or NOMEM. A row of the same id and
// class is reused, its fields replaced: the same handle back.
int moy_app_grant(moy_appabi_t *a, const char *id, size_t n, uint8_t cls,
                  uint32_t roles, int kind, const char *ns, size_t ns_n,
                  uint32_t owner, uint32_t *h);
int moy_app_end(moy_appabi_t *a, uint32_t h);      // OK or STALE
int moy_app_grant_get(const moy_appabi_t *a, uint32_t h, const moy_grant_t **g);
uint32_t moy_app_grants(const moy_appabi_t *a);    // live grants
// The kernel's live grants, 0 before it is made (the KSTOP line's).
uint32_t moy_app_kernel_grants(void);

// -- the policy -------------------------------------------------------------------

// The permission a role is granted by ("" for none) and the role a permission
// names (-1 for none): files, prefs, appearance (theme), launch (nav),
// clipboard. Every other role is never granted.
const char *moy_app_perm_of(int role);
int moy_app_role_of(const char *perm, size_t n);
// The files kinds, by index: docs, drawings, sprites, music. -1 for another.
const char *moy_app_kind_name(int kind);
int moy_app_kind_of(const char *name, size_t n);
#define MOY_APP_DEFAULT_KIND 0  // "files" with no kind: docs

// A manifest's permissions, read one by one (moy_app_policy_add) into what
// they grant. `files` with no kind is the default kind, `files:<kind>` a
// known kind; an unknown kind grants nothing. Two kinds or more are a manifest
// error, and then no files grant is made.
typedef struct {
    uint32_t roles;
    uint8_t nkinds;
    int8_t kinds[MOY_APP_KINDS];    // in declaration order, each once
} moy_app_policy_t;

void moy_app_policy_init(moy_app_policy_t *p);
void moy_app_policy_add(moy_app_policy_t *p, const char *perm, size_t n);
// The grant's mask, and its kind (-1 for none).
uint32_t moy_app_policy_roles(const moy_app_policy_t *p, int *kind);
// The refusal, NUL-terminated into `out` (cap > 0): 0 when there is none,
// else its length (truncated to cap - 1).
size_t moy_app_policy_error(const moy_app_policy_t *p, char *out, size_t cap);

// The key a cart's grant is made under (#162, SPEC.md 3.1): its id when it
// has one, else its title's slug (ASCII letters and digits kept and lowered,
// space, '-' and '_' as '_', the rest dropped; "app" for no title, "cart" for
// a slug left empty). Returns the length, written NUL-terminated up to cap - 1.
size_t moy_app_id_for(const char *id, size_t id_n, const char *title,
                      size_t title_n, char *out, size_t cap);

// -- damage -------------------------------------------------------------------------

// all: the whole surface repaints next frame. again: one more frame, asked
// from within a draw. The frame gate takes both where it folds the console's
// dirty flag (moy_app_damage_take) and drops an `all` its own draw raised
// (moy_app_damage_drop), so a draw's `all` is lost and its `again` is not.
int moy_app_damage_all(moy_appabi_t *a, uint32_t g);
int moy_app_damage_again(moy_appabi_t *a, uint32_t g);
uint32_t moy_app_damage_take(moy_appabi_t *a);     // MOY_DAMAGE_* bits, cleared
void moy_app_damage_drop(moy_appabi_t *a);

// -- prefs ---------------------------------------------------------------------------

// The settings row `<ns>_<key>` of the grant's namespace, as JSON text. get:
// OK and the text's length in `*len` (copied up to cap), or ABSENT; with no
// buffer (`out` NULL) it is the size alone, and counted only when it fails. set: OK
// (the row written, then flushed through the rows' saver: a failed save leaves
// it dirty for the next), BAD (no key, a key too long, `json` not one value)
// or NOMEM. clear: OK, or ABSENT when there was no row.
int moy_app_prefs_get(moy_appabi_t *a, uint32_t g, const char *key, size_t n,
                      char *out, size_t cap, size_t *len);
int moy_app_prefs_set(moy_appabi_t *a, uint32_t g, const char *key, size_t n,
                      const char *json, size_t json_n);
int moy_app_prefs_clear(moy_appabi_t *a, uint32_t g, const char *key, size_t n);

// -- the clipboard -----------------------------------------------------------------

// put_text: OK, or BAD for text over MOY_APP_CLIP_MAX bytes, which keeps the
// old text. text: OK and the length (copied up to cap; with no buffer, the size
// alone, counted only when it fails). kind: MOY_CLIP_*. seq:
// bumped by every put. A grant without the role answers DENIED (kind and seq
// as negatives).
int moy_app_clip_put_text(moy_appabi_t *a, uint32_t g, const char *text, size_t n);
int moy_app_clip_text(moy_appabi_t *a, uint32_t g, char *out, size_t cap, size_t *len);
int32_t moy_app_clip_kind(moy_appabi_t *a, uint32_t g);
int32_t moy_app_clip_seq(moy_appabi_t *a, uint32_t g);

// -- the counters ------------------------------------------------------------------

// Calls of C row `row` since the state was made (the roles trace's coverage
// and the per-frame budgets), and its "role.verb" name.
uint32_t moy_app_count(const moy_appabi_t *a, int row);
const char *moy_app_row_name(int row);
const char *moy_app_role_name(int role);

#endif // MOY_APP_H

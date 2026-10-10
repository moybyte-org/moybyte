// The app ABI's kernel half (docs/kernel_appabi_2026-10.md): the grants, the
// grant policy, and the roles served in C. roles.json beside this file is the
// role table; a C row there is a function here, and tests/test_moy_app.py holds
// the two equal.
//
// A moy_appabi_t is the state the C rows read: the grant table (kind GRANT, rows
// keyed by the cart's id, each with its surface row), the settings rows prefs
// writes into, the damage flags, the live token table, the pointer it is bound
// to and the clipboard. On a board it is the kernel's (moy_app_kernel: made
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
// The shell writes what the surface and theme rows answer: each grant's surface
// row (moy_app_surface_write, at every change of the canvas an app draws on,
// its scales, its window or its bar), the live token table (moy_app_theme_write,
// at every switch of the look) and the pointer (moy_app_pointer_bind, the
// console's moy_input_ptr_t, read live).
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
#include "moy_input.h"
#include "moy_settings.h"
#include "moy_ufiles.h"

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
    MOY_ROW_SURFACE_CANVAS, MOY_ROW_SURFACE_SIZE, MOY_ROW_SURFACE_FONT_SCALE,
    MOY_ROW_SURFACE_CHROME_SCALE, MOY_ROW_SURFACE_WINDOWED, MOY_ROW_SURFACE_BAR_H,
    MOY_ROW_SURFACE_POINTER,
    MOY_ROW_THEME_COLORS, MOY_ROW_THEME_TOKEN, MOY_ROW_THEME_GEN,
    MOY_ROW_THEME_LIGHT, MOY_ROW_THEME_NAME, MOY_ROW_THEME_VARIANT,
    MOY_ROW_THEME_SKIN,
    MOY_ROW_FILES_READABLE, MOY_ROW_FILES_READY, MOY_ROW_FILES_BEGIN,
    MOY_ROW_FILES_END, MOY_ROW_FILES_LIST, MOY_ROW_FILES_COUNT, MOY_ROW_FILES_LOAD,
    MOY_ROW_FILES_SAVE, MOY_ROW_FILES_DELETE, MOY_ROW_FILES_DUPLICATE,
    MOY_ROW_FILES_RENAME, MOY_ROW_FILES_NEW_NAME, MOY_ROW_FILES_TRASH_LIST,
    MOY_ROW_FILES_RESTORE, MOY_ROW_FILES_EMPTY_TRASH, MOY_ROW_FILES_HISTORY,
    MOY_ROW_FILES_HISTORY_OPS, MOY_ROW_FILES_HISTORY_COMMIT,
    MOY_ROW_FILES_ENCODE_IMAGE, MOY_ROW_FILES_DECODE_IMAGE,
    MOY_ROW_FILES_DECODE_COVER, MOY_ROW_FILES_ENCODE_COVER, MOY_ROW_FILES_SIG,
    MOY_ROW_FILES_STAMP, MOY_ROW_FILES_ENCODE_TEXT, MOY_ROW_FILES_DECODE_TEXT,
    MOY_ROW_FILES_PROVENANCE,
    MOY_ROW_PREFS_GET, MOY_ROW_PREFS_SET, MOY_ROW_PREFS_CLEAR,
    MOY_ROW_WALLPAPER_LOAD_COPY, MOY_ROW_WALLPAPER_SAVE_COPY,
    MOY_ROW_ARTWORK_CURRENT, MOY_ROW_ARTWORK_FOLLOW,
    MOY_ROW_CLIPBOARD_PUT_TEXT, MOY_ROW_CLIPBOARD_TEXT, MOY_ROW_CLIPBOARD_KIND,
    MOY_ROW_CLIPBOARD_SEQ,
    MOY_ROW_N
};

#define MOY_GRANT_SLOTS 32u       // grants a table holds
#define MOY_APP_ID_MAX 63u      // an id's, and a prefs namespace's, bytes
#define MOY_APP_KEY_MAX 127u    // a namespaced prefs key's bytes
#define MOY_APP_CLIP_MAX 4096u  // the clipboard's text, in bytes (configuration)
#define MOY_APP_KINDS 4u        // the files kinds a permission may name
#define MOY_APP_NAME_MAX 31u    // a theme's, a variant's and a skin's name bytes
#define MOY_APP_TOKENS 28u      // the token vocabulary's roles
#define MOY_TOKEN_ABSENT INT32_MIN  // a role the live theme does not set
#define MOY_APP_DOC_MAX 255u    // a picture's kind's and name's bytes
#define MOY_APP_ARTWORK_NS "paint"  // the namespace Paint's open picture is kept in
#define MOY_APP_ROOT_MAX 191u   // the carts folder's path, in bytes

enum { MOY_GRANT_SHIPPED = 0, MOY_GRANT_RUN = 1 };
enum { MOY_DAMAGE_ALL = 1u, MOY_DAMAGE_AGAIN = 2u };
enum { MOY_CLIP_EMPTY = 0, MOY_CLIP_TEXT = 1 };

typedef struct moy_app moy_appabi_t;

// What an app's surface row holds: where it draws, at what size and scales,
// whether it is a window on the desk, the host strip's rows over it, and its
// origin on the glass (what the pointer is offset by).
typedef struct {
    uint32_t canvas;            // the CANVAS row it draws on (native/moy_glass), or 0
    int32_t w, h;
    int32_t ox, oy;
    int32_t bar_h;
    uint8_t font_scale, chrome_scale, windowed;
} moy_app_surface_t;

typedef struct {
    uint8_t id_len, ns_len, cls;
    int8_t kind;                // a files kind, 0 .. MOY_APP_KINDS - 1, or -1
    uint32_t roles;
    uint32_t owner;             // an OWNER handle the grant names, or 0
    char id[MOY_APP_ID_MAX + 1];
    char ns[MOY_APP_ID_MAX + 1];
    moy_app_surface_t surf;     // written by the shell, zero until then
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
// The VM ended (native/moy_kernel, before its heap goes): the kernel's state
// lets go of the pointer it was bound to, which lived in that heap.
void moy_app_vm_stop(void);

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

// -- surface ------------------------------------------------------------------------

// The shell's writes. surface_write: `s` into the row of every live grant that
// holds the surface role; the mask of the slots written (a grant's slot is its
// handle's low byte). surface_set: one grant's, OK or STALE.
// pointer_bind: the pointer the rows read, live, or NULL for none.
uint32_t moy_app_surface_write(moy_appabi_t *a, const moy_app_surface_t *s);
int moy_app_surface_set(moy_appabi_t *a, uint32_t g, const moy_app_surface_t *s);
void moy_app_pointer_bind(moy_appabi_t *a, const moy_input_ptr_t *p);

// The rows, each the grant's own surface row. canvas: the CANVAS handle (0
// for a canvas that has none). size: its width and height. The scalar rows
// answer the value, or the code negated. pointer: x and y in the surface's
// coordinates, then down, click and visible as 0/1; ABSENT with no pointer.
int moy_app_surface_canvas(moy_appabi_t *a, uint32_t g, uint32_t *canvas);
int moy_app_surface_size(moy_appabi_t *a, uint32_t g, int32_t *w, int32_t *h);
int32_t moy_app_surface_font_scale(moy_appabi_t *a, uint32_t g);
int32_t moy_app_surface_chrome_scale(moy_appabi_t *a, uint32_t g);
int32_t moy_app_surface_windowed(moy_appabi_t *a, uint32_t g);
int32_t moy_app_surface_bar_h(moy_appabi_t *a, uint32_t g);
int moy_app_surface_pointer(moy_appabi_t *a, uint32_t g, int32_t out[5]);

// -- theme ----------------------------------------------------------------------------

// The token vocabulary (docs/theming_2026-09.md section 4.1): role ids 0 ..
// MOY_APP_TOKENS - 1, their names, and which are flags (true/false) rather
// than MOY64 indices. token_of: the id of a name, or -1.
const char *moy_app_token_name(int role);
int moy_app_token_flag(int role);
int moy_app_token_of(const char *name, size_t n);

// The look's writes: the live theme (its name, its variant and every token,
// MOY_TOKEN_ABSENT for a role it does not set) and the skin. Each bumps the
// generation. OK, or BAD for a name over MOY_APP_NAME_MAX bytes.
int moy_app_theme_write(moy_appabi_t *a, const char *name, size_t n,
                        const char *variant, size_t vn,
                        const int32_t tokens[MOY_APP_TOKENS]);
int moy_app_theme_write_skin(moy_appabi_t *a, const char *skin, size_t n);
// The table whole and its generation, uncounted: what a binding builds its
// colours from.
uint32_t moy_app_theme_read(const moy_appabi_t *a, int32_t tokens[MOY_APP_TOKENS]);

// The rows. colors: the generation a binding's colour dict is keyed on (it
// rebuilds the dict from moy_app_theme_read when it moves). token: one role's
// value, BAD for an unknown role, ABSENT for one the theme does not set. gen
// and light (the surface_light flag) answer the value or the code negated.
// name, variant, skin: OK and the length (copied up to cap; no buffer: the
// size alone, counted only when it fails).
int moy_app_theme_colors(moy_appabi_t *a, uint32_t g, uint32_t *gen);
int moy_app_theme_token(moy_appabi_t *a, uint32_t g, int role, int32_t *v);
int32_t moy_app_theme_gen(moy_appabi_t *a, uint32_t g);
int32_t moy_app_theme_light(moy_appabi_t *a, uint32_t g);
int moy_app_theme_name(moy_appabi_t *a, uint32_t g, char *out, size_t cap, size_t *len);
int moy_app_theme_variant(moy_appabi_t *a, uint32_t g, char *out, size_t cap, size_t *len);
int moy_app_theme_skin(moy_appabi_t *a, uint32_t g, char *out, size_t cap, size_t *len);

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

// -- artwork -----------------------------------------------------------------------

// Paint's open picture: the settings rows `paint_doc_kind` and `paint_doc`, each a
// JSON string (no kind row is the drawings kind), which Paint's model writes
// through its prefs. current: OK and the kind and the name (copied up to each
// cap; with no buffers, the sizes alone, counted only when it fails), or ABSENT
// while no picture is named. follow: when the open picture is `kind`/`old` it
// becomes `kind`/`new` and OK (a file renamed under it); ABSENT when it is
// another; BAD for a name empty, longer than MOY_APP_DOC_MAX or holding a
// control byte. Text crosses as UTF-8; the rows hold it JSON-escaped.
int moy_app_artwork_current(moy_appabi_t *a, uint32_t g, char *kind, size_t kcap,
                            size_t *klen, char *name, size_t ncap, size_t *nlen);
int moy_app_artwork_follow(moy_appabi_t *a, uint32_t g, const char *kind, size_t kn,
                           const char *old, size_t on, const char *nw, size_t nn);

// -- the user-files store ------------------------------------------------------------

// The store the files and wallpaper rows reach (native/moy_store/moy_ufiles.h):
// the console binds the layer's verb table, the carts folder and whether the
// store reads and writes, at its build and at every change; NULL `ops` binds
// none. OK, or BAD for a root over MOY_APP_ROOT_MAX bytes. Every store row takes
// the board's bus gate around its own op (moy_vol_gate_enter), never across
// two.
int moy_app_store_bind(moy_appabi_t *a, const moy_uf_ops_t *ops, const char *root,
                       size_t n, int readable, int writable);
// A row that raised inside the store (a binding's exception path): the gate it
// held is left.
void moy_app_store_unwind(moy_appabi_t *a);
// The errno value of the last store row that answered IO.
int moy_app_why(const moy_appabi_t *a);
// An answer a store row wrote into a moy_buf_t, freed by the layer it came from.
void moy_app_buf_free(moy_appabi_t *a, moy_buf_t *b);
// Sessions left open, ended: how many (the console's, at each frame's start and
// a run's end).
uint32_t moy_app_files_end_all(moy_appabi_t *a);
// Bumped by every save_copy that landed: what the backdrop's decode is keyed on.
uint32_t moy_app_copy_gen(const moy_appabi_t *a);

// -- files ---------------------------------------------------------------------------

// The user-files layer over the bound store (moy_ufiles.h has each verb's
// rules). A grant made with a files kind reaches that kind alone: another
// answers DENIED. A read answers NOSTORE with no store to read, a write with
// none to write; a store that failed answers IO (moy_app_why), nothing there
// ABSENT, a refused argument BAD. Names come back NUL-terminated in `name_out`
// (MOY_UF_NAME_MAX + 1); lists as names, each NUL-terminated, in a buffer
// (moy_app_buf_free). readable and ready answer 1 or 0, or the code negated.
// begin opens a session (NOSTORE with no store to write): readiness and nothing
// more, since every row takes the gate around its own op; end closes one.
int32_t moy_app_files_readable(moy_appabi_t *a, uint32_t g);
int32_t moy_app_files_ready(moy_appabi_t *a, uint32_t g);
int moy_app_files_begin(moy_appabi_t *a, uint32_t g);
int moy_app_files_end(moy_appabi_t *a, uint32_t g);
int moy_app_files_list(moy_appabi_t *a, uint32_t g, const char *kind,
                       moy_buf_t *out, uint32_t *count);
int moy_app_files_count(moy_appabi_t *a, uint32_t g, const char *kind, uint32_t *n);
int moy_app_files_load(moy_appabi_t *a, uint32_t g, const char *kind,
                       const char *name, moy_buf_t *out, int *binary);
int moy_app_files_save(moy_appabi_t *a, uint32_t g, const char *kind,
                       const char *name, const char *data, size_t n, char *name_out);
int moy_app_files_delete(moy_appabi_t *a, uint32_t g, const char *kind,
                         const char *name, char *name_out);
int moy_app_files_duplicate(moy_appabi_t *a, uint32_t g, const char *kind,
                            const char *name, char *name_out);
int moy_app_files_rename(moy_appabi_t *a, uint32_t g, const char *kind,
                         const char *name, const char *title, char *name_out);
// `title` NULL: the kind's auto-name; else that title slugged and made unique.
int moy_app_files_new_name(moy_appabi_t *a, uint32_t g, const char *kind,
                           const char *title, char *name_out);
// Kind and name, each NUL-terminated, `*count` pairs, newest first.
int moy_app_files_trash_list(moy_appabi_t *a, uint32_t g, moy_buf_t *out,
                             uint32_t *count);
int moy_app_files_restore(moy_appabi_t *a, uint32_t g, const char *kind,
                          const char *name, char *name_out);
int moy_app_files_empty_trash(moy_appabi_t *a, uint32_t g);
// The sidecar's records, and the ops after its last keyframe: JSON arrays.
int moy_app_files_history(moy_appabi_t *a, uint32_t g, const char *kind,
                          const char *name, moy_buf_t *out);
int moy_app_files_history_ops(moy_appabi_t *a, uint32_t g, const char *kind,
                              const char *name, moy_buf_t *out);
// The keyframe and the op batch as JSON text (NULL for none); a prune that
// failed is `*prune_err` (an errno value, or MOY_UF_BAD) and not the row's
// failure.
int moy_app_files_history_commit(moy_appabi_t *a, uint32_t g, const char *kind,
                                 const char *name, const char *ops, size_t ops_n,
                                 const char *kf, size_t kf_n, int *prune_err);
// The codecs: no store needed, only the layer (ABSENT with none bound, and for
// a blob that is not one).
int moy_app_files_encode_image(moy_appabi_t *a, uint32_t g, uint32_t w, uint32_t h,
                               const uint8_t *pix, size_t n, moy_buf_t *out);
int moy_app_files_decode_image(moy_appabi_t *a, uint32_t g, const char *text,
                               size_t n, moy_buf_t *pix, uint32_t *w, uint32_t *h);
int moy_app_files_decode_cover(moy_appabi_t *a, uint32_t g, const uint8_t *data,
                               size_t n, moy_buf_t *pix);
int moy_app_files_encode_cover(moy_appabi_t *a, uint32_t g, const uint8_t *pix,
                               size_t n, moy_buf_t *out);
int moy_app_files_sig(moy_appabi_t *a, uint32_t g, const char *text, size_t n,
                      uint32_t *sig);
// ABSENT: not a JSON object, which passes through as it was.
int moy_app_files_stamp(moy_appabi_t *a, uint32_t g, const char *blob, size_t n,
                        const char *kind, const char *name, uint32_t sig,
                        moy_buf_t *out);
// A document is its own text: encode_text and decode_text answer OK, and the
// text crosses as it is (a binding splits decode_text's into lines).
int moy_app_files_encode_text(moy_appabi_t *a, uint32_t g);
int moy_app_files_decode_text(moy_appabi_t *a, uint32_t g);
// OK, src and sig; ABSENT with no stamp.
int moy_app_files_provenance(moy_appabi_t *a, uint32_t g, const char *blob, size_t n,
                             moy_buf_t *src, int64_t *sig);

// -- wallpaper -----------------------------------------------------------------------

// The backdrop's backing picture, `artwork.moyimg` beside the carts folder:
// load_copy its text (ABSENT: never saved), save_copy a write that bumps
// moy_app_copy_gen.
int moy_app_wallpaper_load_copy(moy_appabi_t *a, uint32_t g, moy_buf_t *out);
int moy_app_wallpaper_save_copy(moy_appabi_t *a, uint32_t g, const char *data,
                                size_t n);

// -- the rows served in Python ------------------------------------------------------

// Whether grant `g` holds `role`: OK, STALE or DENIED. A binding asks it before
// it calls the server the console registered for a role's rows that are not C's.
int moy_app_holds(const moy_appabi_t *a, uint32_t g, int role);

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

// -- the ROLE door -----------------------------------------------------------------

// The role table's rows (roles.json), C and shell alike: what the door names.
#define MOY_APP_TABLE_N 111u
// Table row `row`'s role (MOY_ROLE_*), its C row (MOY_ROW_*, or MOY_APP_SHELL
// for a row the Python console serves), -1 past the table; its "role.verb",
// NULL past it.
#define MOY_APP_SHELL (-1)
int moy_app_table_role(uint32_t row);
int moy_app_table_c_row(uint32_t row);
const char *moy_app_table_name(uint32_t row);

// A compiled app's way to any row (docs/kernel_appabi_2026-10.md section 2.1):
// the grant first, table row `row`, its arguments packed in `arg` (n bytes)
// and its answer written into `ans` (cap bytes). It answers >= 0, the row's
// number or the length of the text or blob written, or a MOY_APP_* code
// negated; an answer longer than `cap` is FULL and writes nothing.
//
// THE ARGUMENTS are fields in the row's order, each a little-endian uint32
// length and its bytes; a number is a 4-byte field (int32, little-endian), a
// text field its UTF-8 (a name at most MOY_UF_NAME_MAX bytes), an empty text
// field "none" where the row takes an optional one. A missing or extra field,
// or a number field not 4 bytes long, is BAD. surface.size and surface.pointer
// answer their numbers as little-endian int32s; artwork.current its kind and
// its name as two fields. A list answers its names NUL-separated, as the row's
// buffer holds them.
//
// A C row runs here, its answer copied out (a blob the user-files layer
// allocated freed with moy_app_buf_free). A C row whose shape is an object the
// Python binding hands out (surface.canvas, theme.colors) or a codec over
// pixels answers BAD: it has no door. A shell row whose role the grant holds
// crosses to the Python console through the door bound with
// moy_app_door_bind (moy_loop_role: counted ROLE, and NEEDS_VM with no VM);
// with no door bound it answers NEEDS_VM. Its caller is the VM's task (a
// store row takes the bus gate; a shell row enters Python): a compiled cart's
// session thread reaches it through the hop (moy_wasm_on_vm).
int32_t moy_app_role(moy_appabi_t *a, uint32_t g, uint32_t row, const uint8_t *arg,
                     size_t n, uint8_t *ans, size_t cap);
typedef int32_t (*moy_app_door_fn)(uint32_t g, uint32_t row, const uint8_t *arg,
                                   size_t n, uint8_t *ans, size_t cap);
// The door the shell rows cross: one a process, NULL for none.
void moy_app_door_bind(moy_app_door_fn fn);

// -- the seqlock ------------------------------------------------------------------

// Every write of the look (theme_write, theme_write_skin) and of the clipboard
// takes the state's seqlock, so a reader on another thread (a compiled app's
// session thread, moy_app_wasm.h) reads a row whole: begin, read, and read
// again unless end answers true; an odd begin is a write in progress.
uint32_t moy_app_seq_begin(const moy_appabi_t *a);
int moy_app_seq_end(const moy_appabi_t *a, uint32_t s);

// -- the counters ------------------------------------------------------------------

// Calls of C row `row` since the state was made (the roles trace's coverage
// and the per-frame budgets), and its "role.verb" name.
uint32_t moy_app_count(const moy_appabi_t *a, int row);
const char *moy_app_row_name(int row);
const char *moy_app_role_name(int role);

#endif // MOY_APP_H

// The user-files layer (#108, #111): a person's creations as real files under
// ONE visible root beside the carts folder -- `files/<kind>/<name><ext>` --
// and a project's own folder as a kind of its own (`project:<folder>.moy`).
// What a PC sees on the mounted card is what the Files app shows.
//
//   the kinds        drawings (.moyimg), docs (.md), sprites (.moygfx), music
//                    (.moysong), and recordings, one FOLDER an item. Every verb
//                    validates its kind against them: another is MOY_UF_BAD.
//   the vault        docs holds notes and more: a `.py`, `.lua`, `.txt` or
//                    `.json` is listed and addressed by its WHOLE name, a `.md`
//                    by its stem, so `todo.txt` and `todo` never shadow each
//                    other and the extension picks the editing mode.
//   naming           a title slugs to a name (the store's slug, a vault
//                    extension kept), a collision takes `_2`, `_3`, ... before
//                    the extension, and an item is auto-named `<base>_<n>`. A
//                    title with no ASCII letter or digit names nothing: a NEW
//                    auto-names and a rename keeps the old name.
//   a project kind   `project:<folder>`: the project's own files under the
//                    carts root, PROJECT_ORDER first, then the rest sorted, then
//                    `scenes/*.moyscene` and `images/*.moyimg`. A name holds at
//                    most one folder and never climbs. A save is published and
//                    appended to the project's journal (moy_journal.h); its
//                    cover (`cover.png`) is bytes, published whole and never
//                    journaled. A project has no sidecar, no trash and no names.
//   the trash        a delete moves the item to `files/trash/<kind>/` under a
//                    unique name, its sidecar with it; a restore moves it back;
//                    past MOY_UF_TRASH_KEEP entries the oldest go.
//   the sidecars     `files/.history/<kind>/<name>.jsonl`, the op history: a
//                    keyframe line `{"t": "kf", "doc": ...}` and op-segment lines
//                    `{"t": "seg", "ops": [...]}`, appended raw; a line that is
//                    not one of the two is dropped at read; a commit prunes it to
//                    the newest keyframe and MOY_UF_HISTORY_KEEP segments after
//                    it, each kept line rewritten as json.dumps writes it. The
//                    sidecar follows its file through rename, duplicate, the
//                    trash and back.
//   provenance       a copy's JSON gains `src` ("<kind>/<name>") and `sig` (the
//                    source's content signature): what "your drawing changed"
//                    and "used in" read. Pure metadata.
//   the codecs       a picture's moyimg-v1 blob (moy_img.h decodes it; here it
//                    is written: the ZLIB stream at a 4 KiB window the boards'
//                    `deflate` writes, base64, the JSON header), and a cart's
//                    cover.png (moy_png.h reads it in the console palette; here
//                    it is written indexed, the palette its PLTE).
//   the copy         `artwork.moyimg` beside the carts folder: the wallpaper's
//                    backing picture.
//
// runtime/moy_files.py and runtime/moy_file_ops.py were the reference this was
// ported from, rule for rule; tests/userfiles_workload.py's digests pin the
// bytes they wrote, which this writes. Where it reads otherwise: an item and
// the copy are read through the store's recovering read (moy_fs_read), so a
// save a power cut left torn reads whole (fuzz_fs's matrix cuts each write);
// a title's letters and digits are ASCII's. Listings sort newest first by the
// medium's mtime (seconds; 0 where it keeps none), then by name.
//
// Paths are absolute and resolve through moy_vol_at; `root` is the carts
// folder. Every call returns 0, an errno value (the store failed), MOY_UF_NONE
// (nothing there: Python's None) or MOY_UF_BAD (a refused argument: Python's
// ValueError). A moy_buf_t answer is the caller's to moy_buf_free.

#ifndef MOY_UFILES_H
#define MOY_UFILES_H

#include <stddef.h>
#include <stdint.h>

#include "moy_fs.h"

#define MOY_UF_NONE (-1)
#define MOY_UF_BAD (-2)
#define MOY_UF_NAME_MAX 255u        // a stored name's bytes
#define MOY_UF_KINDS 5
#define MOY_UF_TRASH_KEEP 50u
#define MOY_UF_HISTORY_KEEP 32u
#define MOY_UF_COVER_SIDE 128
#define MOY_UF_COVER_MAX 65536u     // a cover's bytes (cover_png.MAX_BYTES)
#define MOY_UF_WBITS 12             // the ZLIB window a picture is written at
#define MOY_UF_PICTURE_MAX (4096u * 4096u)  // a decode's pixels: past it, no picture

// -- the kinds and the vault ------------------------------------------------------

// Kind `i` (0 .. MOY_UF_KINDS - 1): its name, its extension ("" for the folder
// kind), whether an item is a folder, its auto-name base. NULL past the last.
const char *moy_uf_kind(int i);
const char *moy_uf_kind_ext(int i);
int moy_uf_kind_folder(int i);
const char *moy_uf_kind_base(int i);
int moy_uf_kind_of(const char *kind);       // -1 for another

// The vault extension `name` carries (.py .lua .txt .json), or the script one
// (.py .lua): its length, 0 for none.
size_t moy_uf_vault_ext(const char *name);
size_t moy_uf_script_ext(const char *name);

// The folder a project kind names ("" when `kind` is no project kind): the
// pointer into `kind` past "project:".
const char *moy_uf_project_folder(const char *kind);

// -- paths ----------------------------------------------------------------------------

enum {
    MOY_UF_PATH_FILES = 0,      // the user-files root
    MOY_UF_PATH_KIND,           // files/<kind>
    MOY_UF_PATH_FILE,           // one item
    MOY_UF_PATH_HISTORY,        // its sidecar
    MOY_UF_PATH_TRASH,          // its place in the trash
    MOY_UF_PATH_HISTORY_TRASH,  // its sidecar's place in the trash
    MOY_UF_PATH_PROJECT,        // a project kind's folder
    MOY_UF_PATH_PROJECT_FILE,   // one of its files
};
// The path `which` names, NUL-terminated into `out`.
int moy_uf_path(int which, const char *root, const char *kind,
                const char *name, moy_buf_t *out);

// -- the items --------------------------------------------------------------------------

// The kind's names, newest first, each NUL-terminated, `*count` of them.
int moy_uf_list(const char *root, const char *kind, moy_buf_t *out,
                uint32_t *count);
int moy_uf_count(const char *root, const char *kind, uint32_t *count);
// An item's text (a project's cover: its bytes, `*binary` set), or NONE.
int moy_uf_load(const char *root, const char *kind, const char *name,
                moy_buf_t *out, int *binary);
// The stored name each answers, NUL-terminated, into `name_out`
// (MOY_UF_NAME_MAX + 1 bytes).
int moy_uf_save(const char *root, const char *kind, const char *name,
                const char *data, size_t n, char *name_out);
int moy_uf_new_name(const char *root, const char *kind, const char *base,
                    char *name_out);    // `base` NULL: the kind's own
int moy_uf_free_name(const char *root, const char *kind, const char *title,
                     char *name_out);
int moy_uf_rename(const char *root, const char *kind, const char *name,
                  const char *title, char *name_out);
int moy_uf_duplicate(const char *root, const char *kind, const char *name,
                     char *name_out);
int moy_uf_delete(const char *root, const char *kind, const char *name,
                  char *name_out);
int moy_uf_restore(const char *root, const char *kind, const char *name,
                   char *name_out);

// -- the trash ---------------------------------------------------------------------------

// Every trashed item, newest first across kinds: kind and name, each
// NUL-terminated, `*count` pairs.
int moy_uf_trash_list(const char *root, moy_buf_t *out, uint32_t *count);
int moy_uf_prune_trash(const char *root, uint32_t keep);

// -- the sidecars ------------------------------------------------------------------------

// The records, as a JSON array of the sidecar's good lines; the ops after the
// last keyframe, as one JSON array. A project kind answers "[]".
int moy_uf_history(const char *root, const char *kind, const char *name,
                   moy_buf_t *out);
int moy_uf_history_ops(const char *root, const char *kind, const char *name,
                       moy_buf_t *out);
// The keyframe (JSON text, NULL for none) then the op batch (a JSON array as
// text, NULL for none) appended, each as CPython's json.dumps writes it
// whatever wrote the text (BAD for text that is not one JSON value), then the
// prune; a prune that failed is
// `*prune_err` (an errno value, or MOY_UF_BAD), counted, and not this call's
// failure. Nothing to write writes nothing.
int moy_uf_history_commit(const char *root, const char *kind, const char *name,
                          const char *ops, size_t ops_n, const char *kf,
                          size_t kf_n, int *prune_err);
int moy_uf_prune_history(const char *root, const char *kind, const char *name,
                         uint32_t keep, uint32_t *dropped);
int moy_uf_clear_history(const char *root, const char *kind, const char *name);
uint32_t moy_uf_prune_fails(void);   // prunes history_commit swallowed

// -- provenance -------------------------------------------------------------------------

// The content signature of a text (its code points: moyimg's text_sig), 0 for
// an empty one.
uint32_t moy_uf_sig(const char *text, size_t n);
// `blob` stamped with src and sig, or NONE when it is not a JSON object (it
// passes through unchanged).
int moy_uf_stamp(const char *blob, size_t n, const char *kind, const char *name,
                 uint32_t sig, moy_buf_t *out);
// A stamped blob's src (decoded, into `src`) and sig: 0, or NONE for no stamp.
int moy_uf_provenance(const char *blob, size_t n, moy_buf_t *src, int64_t *sig);

// -- the codecs ------------------------------------------------------------------------

int moy_uf_encode_image(uint32_t w, uint32_t h, const uint8_t *pix, size_t n,
                        moy_buf_t *out);
// The picture's w*h indices (NONE: not a picture).
int moy_uf_decode_image(const char *text, size_t n, moy_buf_t *pix,
                        uint32_t *w, uint32_t *h);
int moy_uf_encode_cover(const uint8_t *pix, size_t n, moy_buf_t *out);
int moy_uf_decode_cover(const uint8_t *data, size_t n, moy_buf_t *pix);
// The console palette a cover is written in and read to: 64 R, G, B triples.
const uint8_t *moy_uf_palette(void);

// -- the wallpaper's copy ------------------------------------------------------------------

int moy_uf_copy_load(const char *root, moy_buf_t *out);     // NONE: never saved
int moy_uf_copy_save(const char *root, const char *data, size_t n);

// -- the table a binding hands on ---------------------------------------------------------

// The layer as one table of its verbs, for code that is linked without it
// and handed it (native/moy_app's files and wallpaper rows: moy_app_store_bind):
// the store's file verbs it reaches through are this layer's own.
typedef struct moy_uf_ops {
    int (*list)(const char *, const char *, moy_buf_t *, uint32_t *);
    int (*count)(const char *, const char *, uint32_t *);
    int (*load)(const char *, const char *, const char *, moy_buf_t *, int *);
    int (*save)(const char *, const char *, const char *, const char *, size_t, char *);
    int (*new_name)(const char *, const char *, const char *, char *);
    int (*free_name)(const char *, const char *, const char *, char *);
    int (*rename)(const char *, const char *, const char *, const char *, char *);
    int (*duplicate)(const char *, const char *, const char *, char *);
    int (*del)(const char *, const char *, const char *, char *);
    int (*restore)(const char *, const char *, const char *, char *);
    int (*trash_list)(const char *, moy_buf_t *, uint32_t *);
    int (*prune_trash)(const char *, uint32_t);
    int (*history)(const char *, const char *, const char *, moy_buf_t *);
    int (*history_ops)(const char *, const char *, const char *, moy_buf_t *);
    int (*history_commit)(const char *, const char *, const char *, const char *,
                          size_t, const char *, size_t, int *);
    uint32_t (*sig)(const char *, size_t);
    int (*stamp)(const char *, size_t, const char *, const char *, uint32_t, moy_buf_t *);
    int (*provenance)(const char *, size_t, moy_buf_t *, int64_t *);
    int (*encode_image)(uint32_t, uint32_t, const uint8_t *, size_t, moy_buf_t *);
    int (*decode_image)(const char *, size_t, moy_buf_t *, uint32_t *, uint32_t *);
    int (*encode_cover)(const uint8_t *, size_t, moy_buf_t *);
    int (*decode_cover)(const uint8_t *, size_t, moy_buf_t *);
    int (*copy_load)(const char *, moy_buf_t *);
    int (*copy_save)(const char *, const char *, size_t);
    void (*buf_free)(moy_buf_t *);
    int (*gate_enter)(const char *);
    void (*gate_leave)(int);
} moy_uf_ops_t;

extern const moy_uf_ops_t moy_uf_ops;

// -- tests -------------------------------------------------------------------------------

// The time a project save's journal entry records: `ts`, or the clock's for
// a negative one (the default).
void moy_uf_clock_fix(int64_t ts);

#endif // MOY_UFILES_H

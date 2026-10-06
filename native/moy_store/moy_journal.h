// The journal (docs/kernel_store_2026-10.md section 7): a cart's durable
// undo history, on the medium as runtime/moy_journal.py keeps it.
//
// moy_journal.py's header is the reference and the crash-safety story every
// call here follows: `journal/journal.jsonl` appended one line a commit, the
// snapshot under `journal/s/` put down first (a claimed publish backup where
// the stamp matches, else written), the line next, the per-file cursor map
// (`cursor.json`, which exists only while something is rewound) last; the
// 64-entry, 512 KiB budget; a line is json.dumps's text, so a journal one
// tier writes reads the same on every other. A line that is not an object
// with an int "seq" and string "file" and "snap" is dropped (strict reader).
//
// #136's reads: the entries of a file, a snapshot by its seq, and a restore,
// which appends the snapshot as a new commit, so history only grows.
//
// `cart` is the cart folder's path; `files` (n of them, NULL for every file)
// scopes a walk to an editor tab's files. Every call returns 0 or an errno
// value unless it says otherwise.

#ifndef MOY_JOURNAL_H
#define MOY_JOURNAL_H

#include <stddef.h>
#include <stdint.h>

#include "moy_fs.h"

#define MOY_JOURNAL_MAX_ENTRIES 64
#define MOY_JOURNAL_MAX_BYTES (512u * 1024u)

// A commit of `file`'s `data`: `*seq` its seq, or 0 when the file's current
// snapshot already holds these bytes and nothing was written. `grad` is the
// graduation rider (0, 1, or -1 for none); `ops` the op batch as JSON text
// (NULL for none), stored as json.dumps writes it; `ts` the time it records.
int moy_journal_append(const char *cart, const char *file, const char *data,
                       size_t n, int grad, const char *ops, size_t ops_n,
                       int64_t ts, uint32_t *seq);

// Undo and redo: 1 and the restored file's name in `out` (NUL-terminated, cut
// at `cap`), 0 at the floor or the ceiling or over a damaged snapshot, or a
// negative errno value.
int moy_journal_undo(const char *cart, const char *const *files, size_t nfiles,
                     char *out, size_t cap);
int moy_journal_redo(const char *cart, const char *const *files, size_t nfiles,
                     char *out, size_t cap);
// Whether an undo (redo != 0: a redo) would restore something: 1 or 0.
int moy_journal_can(const char *cart, int redo, const char *const *files,
                    size_t nfiles);
// Entries dropped to bring the journal within its budget (>= 0), or a
// negative errno value.
int moy_journal_compact(const char *cart);

// The entries of `file` (NULL: every file), oldest first: `fn` gets each
// line's text, and a nonzero return stops the walk and is returned.
typedef int (*moy_jent_fn)(void *ctx, uint32_t seq, const char *line, size_t n);
int moy_journal_list(const char *cart, const char *file, moy_jent_fn fn,
                     void *ctx);
// The snapshot of entry `seq`, checked as an undo checks it: 0 and the text,
// MOY_FS_NONE when there is none or it is damaged.
int moy_journal_snap(const char *cart, uint32_t seq, moy_buf_t *out);
// Entry `seq`'s snapshot published over its file and appended as a new
// commit: `*seq_out` the new seq (0: the file already held it), MOY_FS_NONE
// when the snapshot is gone or damaged.
int moy_journal_restore(const char *cart, uint32_t seq, int64_t ts,
                        uint32_t *seq_out);

// The manifest's "graduated" set to `value`, as moy_carts' setter does: 1
// written, 0 already so or no manifest that reads.
int moy_journal_graduate(const char *cart, int value);

#endif // MOY_JOURNAL_H

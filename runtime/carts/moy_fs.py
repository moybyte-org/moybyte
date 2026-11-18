# Crash-safe file primitives shared by the .moy store (moy_carts) and the
# undo/redo journal (moy_journal) -- extracted from moy_carts.py so the journal
# can be its own module without a circular import. The bodies are
# native/moy_store's (moy_fs.h) on every tier: the module `moy_store`, which
# every image links and the CPython host registers over ctypes
# (runtime/host/moy_store_binding.py). What is here is the story they keep and
# the few verbs made of them. MicroPython-safe.
#
# THE CRASH-SAFETY STORY (the ONE every durable store write in the console rides
# on; #154). A save is THREE writes and nothing else -- no stat, no remove, no
# rename:
#
#   1. `<root>/.publish` <- ONE line naming the file about to be published and
#      its stamp: "#moyfs1 <chars> <crc32> <path>\n". One marker per store root,
#      overwritten in place, never cleared -- the next save overwrites it. Only
#      one publish is ever in flight, so one line says everything.
#   2. `<path>.bak`      <- a STAMP LINE ("#moyfs1 <chars> <crc32>\n") + the new
#      text. The stamp describes the text that follows it, so the backup can say
#      whether it is whole.
#   3. `<path>`          <- the new text, overwritten in place, byte for byte
#      what every other reader in the system expects. Nothing is ever added to
#      the published file: a cart's main.py, a manifest, a sprite blob are
#      exactly their own bytes, so the store scan, the web sync RPC and tools/
#      see no header and no trailer.
#
# WHY THE MARKER EXISTS, AND IT IS NOT BELT AND BRACES. Without it the reader has
# to open `<path>.bak` on EVERY read to learn whether the file beside it is whole
# -- and on littlefs a path lookup is the expensive op (#198: 98 block reads), so
# a P4 boot, which reads every manifest in the store through `_read_recover`,
# measured +6.0s of boot-to-desk and +25% of store scan for it (75 carts). The
# marker turns that per-file lookup into ONE read per process: the reader loads
# the line lazily, and a path the line does not name is returned with no extra
# lookup at all. The T-Deck's FAT never showed the cost -- two reads per lookup
# there -- which is exactly why this is a marker and not a per-board tune.
#
# The `.bak` is a redo log for ONE failure, and `_read_recover` is deliberately
# narrow about which. For the file the marker names, it asks whether the
# published text is a strict PREFIX of the backup's payload -- because that is
# exactly what an interrupted publish leaves: FAT truncates on open-for-write and
# then grows the file, so a power loss lands it somewhere between empty and
# whole, and never anywhere else. Only then does the backup win. A power loss
# lands in one of these places:
#
#   * mid step 1  -- the marker is torn, so it does not parse (the line must end
#                    in a newline) and names nothing; on littlefs it is
#                    copy-on-write and still names the PREVIOUS publish, which
#                    completed. Either way nothing else was touched: `path` holds
#                    the previous save, whole, and the reader trusts it.
#   * mid step 2  -- the marker names `path`, `path` still holds the previous
#                    save and the backup fails its own stamp. A torn backup is
#                    refused rather than published, so the reader keeps `path`.
#   * mid step 3  -- `path` is a prefix of the backup's payload. The reader
#                    republishes the backup, and the save completes. This is the
#                    window the old rename dance could not see: `_read_recover`
#                    used to fall back only when `path` was MISSING, so a short
#                    file read as good.
#   * between 2+3 -- the backup is stamped and whole, `path` still holds the
#                    previous save, which is generally not a prefix of the new
#                    one. THE PREVIOUS SAVE IS WHAT SURVIVES -- the same
#                    guarantee the pre-#154 rename dance gave, and the same one
#                    littlefs gives on its own (it is copy-on-write, so an
#                    interrupted publish there leaves the old file rather than a
#                    torn one, which is this case and not the one above). The
#                    redo log's value is the FAT torn write, which nothing else
#                    can see.
#
# The marker is best-effort at the writing end: if it cannot be written the save
# still goes through, one save without torn-write detection rather than a save
# refused on a medium that is already failing. With NO root registered
# (`set_publish_root`), the reader falls back to opening `<path>.bak` per read --
# which is the pre-marker behaviour, correct and slower, and what a bare
# `moy_journal` on a tmp dir gets.
#
# Anything else at `path` was put there DELIBERATELY by someone that is not this
# module -- `tools/push_cart.py` places a file with remove+rename over the dev
# channel, and a board's kid may have saved that same file first. Reverting a
# push to a stale backup would be a silent undo of the developer's write, so the
# reader trusts `path` and drops the stale `.bak` on the spot. That is also why
# a same-length wrong-content publish is not recoverable: it is indistinguishable
# from a foreign write, and guessing wrong costs more than it saves.
#
# A torn `.bak` is refused rather than published -- garbage never overwrites a
# whole file. When the stamp is absent entirely the file is a LEGACY backup (the
# pre-#154 rename dance, or `moy_sync._publish`'s rotation) and is read as raw
# text, which is what those writers left behind.
#
# `_forget_bak` is the same drop, done by the writer rather than the next reader:
# a foreign writer that calls it leaves nothing stale behind at all.
#
# A SAVE THAT GIVES A FRAME BACK between store ops (`_write_atomic_steps`: an
# install's record, the install prompt's consent) makes the same three writes
# in two ops: the backup first, then the marker and the publish together. Until
# the marker names `path` its previous save is what reads (mid-backup, the
# backup is not consulted at all); after it the backup is whole, so a torn
# publish is the prefix case above. Keeping the marker beside the publish is
# what lets another file's save land between the two ops: that save takes the
# one marker, and this one takes it back in the op that publishes. The second
# op checks the backup's stamp first: when it is not this save's (a save of
# the SAME file came between, or, outside every root, a read dropped it as
# stale) it makes the whole save instead.
#
# THE BACKUP IS SPENT ONCE STEP 3 RETURNS, and `_claim_bak` is what that buys. The
# marker's stamp then fits the published file, so no read reaches the backup again
# before the next save overwrites it -- which means a second writer that wants those
# same bytes on the medium can take the FILE rather than put the payload down twice.
# The undo journal's snapshots are that writer (#154): claiming one is a rename where
# writing it is 7.1 ms per KB on a card. The claimant keeps the stamp line and reads
# back through `_read_stamped`, and `path` then has no backup until its next save.

import moy_store as _native

try:
    from binascii import crc32 as _crc32
except ImportError:  # pragma: no cover -- every target ships binascii
    def _crc32(data, seed=0):
        h = seed
        for b in data:
            h = (h * 31 + b) & 0xFFFFFFFF
        return h


_STAMP = "#moyfs1 "
_PUBLISH = ".publish"

# The store's verbs (native/moy_store/moy_fs.h, modmoy_store.c), by the names
# the store modules call them. `_remove` and `_mkdir` ignore an error; `_read`
# raises OSError for a missing file; `_read_bytes` is None over its cap.
set_publish_root = _native.set_publish_root
_read = _native.read
_write = _native.write
_read_bytes = _native.read_bytes
_write_bytes = _native.write_bytes
_remove = _native.remove
_mkdir = _native.mkdir
_exists = _native.exists
_stamp_of = _native.stamp
_bak_stamp = _native.bak_stamp
_read_stamped = _native.read_stamped
_unmark = _native.unmark
_write_atomic = _native.publish
_publish_bak = _native.publish_bak
_publish_mark = _native.publish_mark
_forget_bak = _native.forget_bak
_claim_bak = _native.claim
_read_recover = _native.read_recover
_pmem_read = _native.pmem_read
_pmem_save = _native.pmem_save
_save_wait = _native.save_wait


def _copy(src, dst):
    """Copy a file by read/write (no shutil on MicroPython). Overwrites the
    destination in place, so the previous good file is never deleted ahead of a
    successful copy."""
    _write(dst, _read(src))


def _stamp_line(text):
    """The stamp line a backup of `text` leads with."""
    n, crc = _stamp_of(text)
    return "%s%d %d\n" % (_STAMP, n, crc)


def _read_bak(path):
    """The text of the backup beside `path` -- see `_read_stamped`."""
    return _read_stamped(path + ".bak")


def _write_atomic_steps(path, data):
    """`_write_atomic`'s save as two store ops, a resume each, for a caller
    that gives a frame back between them: the stamped backup, then the marker
    and the publish together. Every cut lands in a case the module docstring
    covers -- before the marker names `path` its previous save is what reads,
    after it the backup is whole -- and a save of ANOTHER file between the two
    (which takes the one marker) cannot leave this publish unmarked. A backup
    that is no longer this save's makes the second op the whole save
    (`_publish_mark`)."""
    _publish_bak(path, data)
    yield
    _publish_mark(path, data)

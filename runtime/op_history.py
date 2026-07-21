# Universal op-history core (#111 Phase 1): ONE undo/redo primitive shared by
# every editor and Desk Lab app -- the in-RAM half of the keyframe+ops model.
#
# The console has two persistence layers that keep full-file SNAPSHOTS as the
# source of truth (the per-project journal, moy_journal; the user-file sidecars,
# moy_carts). This module is the fine-grained layer that lives BETWEEN snapshots:
# a `History` per open document accumulates the small, JSON-able ops a surface
# emits (a paint stroke, a sheet cell commit, a writer typing burst), gives the
# surface undo/redo over them WITHOUT any I/O, and hands the batch of ops since
# the last commit to whichever persistence adapter is wired (embedded in a
# journal commit line, or appended to a user-file sidecar segment).
#
# MicroPython-safe by construction (this freezes onto both device builds): plain
# classes, no dataclasses, no typing imports -- just lists and dicts. Ops must be
# JSON-able tuples/dicts of ints/strings so they survive the round-trip through
# the persistence layer unchanged.
#
# Two undo strategies, chosen per codec:
#   * INVERT (preferred): the codec knows how to reverse an op in place
#     (paint carries pre-image spans, sheets the old cell value, writer the
#     deleted/inserted text). undo() just calls codec.invert(doc, op) -- O(op).
#   * REPLAY-FROM-KEYFRAME (fallback): the codec can't cheaply invert, but it can
#     snapshot/restore the whole doc. undo() restores the session base snapshot
#     and re-applies every remaining op. Correct for any op, at O(n) per undo.
# A codec supplies apply(doc, op) always, plus EITHER invert(doc, op) OR the
# snapshot(doc)/restore(doc, snap) pair. History picks invert when present.
#
# Granularity + caps are the surface's job (chunk typing on pause, coalesce a
# drag into one stroke op); History only enforces the segment cap: once
# MAX_OPS_PER_SEGMENT ops have accumulated since the last persisted keyframe,
# needs_keyframe() goes True so the adapter writes a fresh full snapshot instead
# of an ever-growing op tail (bounding replay cost + sidecar size).

MAX_OPS_PER_SEGMENT = 256   # >= this many ops since the last keyframe -> force one


class OpCodec(object):
    """The per-editor adapter `History` drives. Duck-typed, not enforced -- a
    codec is any object with these methods; subclassing is optional and only
    documents intent. Required:

        apply(doc, op)          re-apply `op` to `doc` (used by redo, and by the
                                replay-fallback undo). Mutates `doc` in place.

    Then EITHER (invert path, preferred):

        invert(doc, op)         reverse `op`'s effect on `doc` in place, using the
                                pre-image data carried in `op`.

    OR (replay-from-keyframe fallback):

        snapshot(doc) -> blob   a JSON-able, standalone copy of `doc`'s full state.
        restore(doc, blob)      reset `doc` to a snapshot() blob, in place.

    Supplying invert AND snapshot/restore is fine: invert drives undo (cheap),
    and snapshot() is still used to persist keyframes. `op` values must be small
    JSON-able tuples/dicts of ints/strings (MicroPython-safe, frozen on device)."""

    def apply(self, doc, op):
        raise NotImplementedError("OpCodec.apply")


def _has(codec, name):
    fn = getattr(codec, name, None)
    return callable(fn)


# -- shared text-burst codec (Code editor tab + Writer app, #111) -------------
# Both surfaces edit the SAME CodeEditor buffer core and want the SAME undo grain
# (one op per typing/delete BURST). The diff + codec live here so neither owns a
# private copy -- op_history is already the one shared module (the alternative is
# a grab-bag helper file the spec warns against).

def text_diff_op(before, after):
    """The smallest ("edit", pos, deleted, inserted) turning `before` into
    `after` -- a common-prefix/suffix diff over two text snapshots, so one op
    carries a whole typing/delete burst's net change. `pos` + the two strings
    make TextEditCodec.invert a trivial swap (ints/strings only -- MicroPython-
    safe, frozen on device). Returns None for a no-op pair."""
    n = min(len(before), len(after))
    i = 0
    while i < n and before[i] == after[i]:
        i += 1
    max_suffix = n - i
    j = 0
    while j < max_suffix and before[len(before) - 1 - j] == after[len(after) - 1 - j]:
        j += 1
    deleted = before[i:len(before) - j]
    inserted = after[i:len(after) - j]
    if not deleted and not inserted:
        return None
    return ("edit", i, deleted, inserted)


def _place_text_offset(doc, off):
    """Land a text-buffer doc's caret at an absolute flat-text offset -- used
    after apply()/invert() rewrite the buffer via set_text() (which resets
    row/col to the top). Newline counting + the doc's own public goto_row(), no
    reach into private row/col-offset helpers."""
    text = doc.text()
    off = max(0, min(len(text), off))
    row = text.count("\n", 0, off)
    col = off - (text.rfind("\n", 0, off) + 1)
    doc.goto_row(row, col)


class TextEditCodec(OpCodec):
    """OpCodec over any text-buffer doc exposing text()/set_text()/goto_row()
    (the shared CodeEditor) -- one op is a whole typing/delete BURST's net effect
    ("edit", pos, deleted, inserted) as flat character offsets into the doc's
    joined text. apply() and invert() are the SAME shape with deleted/inserted
    swapped, so invert never needs a base snapshot -- History picks the (preferred)
    invert path automatically. snapshot() backs History.keyframe() only (the
    sidecar's full-text keyframe blob when the segment cap trips); undo itself
    never calls it. Used by the Code editor tab (#111 phase 4) and the Writer app
    (phase 3)."""

    def apply(self, doc, op):
        _, pos, deleted, inserted = op
        text = doc.text()
        doc.set_text(text[:pos] + inserted + text[pos + len(deleted):])
        _place_text_offset(doc, pos + len(inserted))

    def invert(self, doc, op):
        _, pos, deleted, inserted = op
        text = doc.text()
        doc.set_text(text[:pos] + deleted + text[pos + len(inserted):])
        _place_text_offset(doc, pos + len(deleted))

    def snapshot(self, doc):
        return doc.text()


class History(object):
    """One per open document. In-RAM undo/redo over the ops a surface records,
    plus the pending batch a persistence adapter drains at commit time.

    The undo FLOOR is the session base (the doc as it was when History was
    constructed) -- walking past it is the persistence layer's job (load the
    previous keyframe + replay its segment), exactly as the journal already
    documents ("finer, in-session undo stays in the editor's RAM")."""

    def __init__(self, doc, codec, max_ops=MAX_OPS_PER_SEGMENT, max_undo=None):
        self.doc = doc
        self.codec = codec
        self.max_ops = int(max_ops) if max_ops else MAX_OPS_PER_SEGMENT
        # An OPTIONAL hard bound on the in-RAM undo stack DEPTH (device RAM is
        # scarce -- the paint/map editors keep a bounded ring, #90/#91): once more
        # than max_undo ops sit on the undo stack the OLDEST is dropped, so recent
        # edits stay undoable while older ones fall back to the persistence layer's
        # snapshots. Sound only with an INVERT codec (a replay codec re-applies
        # from the session base, so it must keep EVERY op); None (the default) =
        # unbounded -- the segment cap alone governs (keyframes bound what persists).
        self.max_undo = int(max_undo) if max_undo else None
        self._undo = []                 # applied ops, oldest .. newest (the undo stack)
        self._redo = []                 # undone ops (newest first pop) -> redo
        self._pending = []              # ops recorded since the last flush()
        self._since_keyframe = 0        # ops persisted since the last keyframe (cap gate)
        self._invert = _has(codec, "invert")
        if not self._invert:
            if not (_has(codec, "snapshot") and _has(codec, "restore")):
                raise ValueError(
                    "OpCodec needs invert() or snapshot()+restore() for undo")
            # Replay fallback: capture the session-base snapshot up front. The doc
            # is at its committed/open state here (record() is called AFTER the
            # surface applies each op, so we must baseline before the first op).
            self._base = codec.snapshot(doc)
        else:
            self._base = None

    # -- recording -------------------------------------------------------------

    def record(self, op):
        """Push `op` (the surface has ALREADY applied it to the doc): grow the
        undo stack, clear the redo stack (a new action forks the timeline --
        the Google-Docs rule), and queue it for the next flush(). No I/O."""
        self._undo.append(op)
        self._cap_undo()
        if self._redo:
            self._redo = []
        self._pending.append(op)
        self._since_keyframe += 1
        return op

    def _cap_undo(self):
        """Enforce the optional max_undo depth bound: drop the OLDEST undo op(s)
        once the stack grows past it (the paint/map RAM ring, #90/#91). A no-op
        when max_undo is None -- the default unbounded History."""
        if self.max_undo:
            while len(self._undo) > self.max_undo:
                del self._undo[0]

    # -- undo / redo -----------------------------------------------------------

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        """Reverse the newest op on the doc and move it to the redo stack.
        Returns the op, or None at the floor (nothing to undo)."""
        if not self._undo:
            return None
        op = self._undo.pop()
        if self._invert:
            self.codec.invert(self.doc, op)
        else:
            # Replay: reset to the session base, re-apply everything still on the
            # undo stack (i.e. all ops EXCEPT the one just popped).
            self.codec.restore(self.doc, self._base)
            for o in self._undo:
                self.codec.apply(self.doc, o)
        self._redo.append(op)
        # Net-cancel an op that hasn't been persisted yet, so a record-then-undo
        # inside one commit window never ships the cancelled op. Ops recorded
        # BEFORE the last flush are out of _pending's scope -- the adapter's
        # snapshot at each commit is their source of truth (as the spec says).
        if self._pending and self._pending[-1] is op:
            self._pending.pop()
            if self._since_keyframe > 0:
                self._since_keyframe -= 1
        return op

    def redo(self):
        """Re-apply the most recently undone op and move it back to the undo
        stack. Returns the op, or None (nothing to redo)."""
        if not self._redo:
            return None
        op = self._redo.pop()
        self.codec.apply(self.doc, op)
        self._undo.append(op)
        self._cap_undo()
        self._pending.append(op)
        self._since_keyframe += 1
        return op

    # -- persistence seam (drained by the adapter at commit time) --------------

    def flush(self):
        """Hand the batch of ops recorded since the last flush() to the caller
        and clear it. Returns a list (possibly empty). The adapter embeds this in
        a journal commit line's additive `ops` field, or appends it as a
        user-file sidecar segment. No I/O here -- the adapter owns the write."""
        batch = self._pending
        self._pending = []
        return batch

    def peek(self):
        """The pending batch WITHOUT draining it (for a can-commit check)."""
        return list(self._pending)

    def needs_keyframe(self):
        """True once >= max_ops ops have accumulated since the last persisted
        keyframe: the adapter should write a fresh full snapshot rather than
        appending yet another segment (bounds replay cost + on-disk size)."""
        return self._since_keyframe >= self.max_ops

    def mark_keyframe(self):
        """The adapter calls this right after it persists a full keyframe: the
        op-since-keyframe counter resets, so the cap gate reflects only the ops
        that will ride on TOP of the new snapshot."""
        self._since_keyframe = 0

    def keyframe(self):
        """A JSON-able full snapshot of the live doc for the adapter to persist
        as a keyframe, or None when the codec can't snapshot (invert-only codecs
        that never keyframe in RAM -- the persistence layer keeps its own
        full-file snapshots regardless)."""
        if _has(self.codec, "snapshot"):
            return self.codec.snapshot(self.doc)
        return None

    # -- reopen seam (rebuild undo depth from a persisted sidecar/journal) -----

    # -- lifecycle -------------------------------------------------------------

    def clear(self):
        """Drop all in-RAM history (a fresh open / a discard). Re-baselines the
        replay snapshot to the doc's current state."""
        self._undo = []
        self._redo = []
        self._pending = []
        self._since_keyframe = 0
        if not self._invert:
            self._base = self.codec.snapshot(self.doc)

    def seed(self, ops):
        """Rebuild undo DEPTH from ops the persistence layer already has on disk
        (a reopened document's flattened sidecar/journal segments, oldest ..
        newest) -- NOT fresh edits: appends straight onto the undo stack and the
        keyframe-since counter, but never touches `_pending`/`_redo` (these ops
        are already committed; re-flushing them would double-write the sidecar,
        and there is nothing to redo into a session that just started). The doc
        itself is assumed already loaded at the state these ops produce (the
        adapter's normal load path), so seed() never calls apply() -- it only
        makes undo() able to walk back through them. Call once, right after
        construction, before any record(). A no-op for an empty/None list."""
        if not ops:
            return
        self._undo.extend(ops)
        self._since_keyframe += len(ops)

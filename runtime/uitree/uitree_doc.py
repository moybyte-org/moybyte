"""The engine's side of a `ui.doc`: today's text machinery under one object.

The buffer, caret, selection, find, completion and Go to are the Code tab's
`CodeEditor` (runtime/editors/editors_code.py); undo is the op journal the
cart editor handle keeps (runtime/editors/op_history.py, a burst closed at
Enter, punctuation and an undo press). Nothing here is the app's: a handler
reaches it through `uitree.DocView`, a template through `uitree.DOC_FIELDS`,
and the text editor node feeds it typed keys and draws it.

A host that already owns a buffer and an undo journal (the Editor's Code
tab: the console's `CodeEditor` and its per-tab history) hands both in with
`adopt`; the document is then a view over them, and its bursts, undo and
edits go to the host's journal.

Lines and columns cross the boundary from 1; `CodeEditor` counts from 0."""
from editors_code import CodeEditor
from op_history import History, TextEditCodec, text_diff_op

_BREAKS = ".,;:"


class Journal:
    """Where an adopted document's bursts, undo and edits go: the host's.
    Every method is the host's to override; this one keeps nothing."""

    def open(self):
        """A burst of typing begins (called again while it is open)."""

    def close(self):
        """The open burst ends: one undo step."""

    def undo(self):
        return False

    def redo(self):
        return False

    def can_undo(self):
        return False

    def can_redo(self):
        return False

    def edited(self):
        """The text changed (the host re-checks what it marks on it)."""


class DocBuf:
    def __init__(self, text, lang):
        self.lang = lang
        self.ed = CodeEditor(text, 80, 40)
        self.history = History(self.ed, TextEditCodec())
        self.journal = None       # the host's, when adopted
        self._burst = None
        self.find_q = ""          # the query the node's find= names, this frame
        self.find_ci = True       # ...matched in either case
        self._ck = None           # the last count and what it was counted for
        self.marks = ()           # (line from 1, kind, words), the node's marks= model
        self.gen = 0              # moves on every change the view draws

    def adopt(self, ed, journal, lang=None):
        """Show and edit the host's buffer `ed` (a CodeEditor), its bursts and
        undo the host's `journal` (a Journal). Called again when the host's
        buffer is replaced (another script opened)."""
        if ed is self.ed and journal is self.journal and (lang is None or lang == self.lang):
            return
        self.ed = ed
        self.journal = journal
        self.history = None
        self._burst = None
        if lang is not None:
            self.lang = lang
        self.gen += 1

    # -- what templates read ------------------------------------------------

    @property
    def line(self):
        return self.ed.row + 1

    @property
    def col(self):
        return self.ed.col + 1

    @property
    def lines(self):
        return len(self.ed.lines)

    @property
    def sel(self):
        return len(self.ed.selected_text()) if self.ed.has_selection() else 0

    @property
    def edits(self):
        return self.ed.edits

    @property
    def can_undo(self):
        if self.journal is not None:
            return self.journal.can_undo()
        return self.history.can_undo() or (self._burst is not None and self._burst != self.ed.text())

    @property
    def can_redo(self):
        if self.journal is not None:
            return self.journal.can_redo()
        return self.history.can_redo()

    @property
    def word(self):
        return self.ed.word_prefix()

    def _count(self):
        e = self.ed
        k = self._ck
        if (k is not None and k[0] is e and k[1] == e.edits and k[2] == self.find_q
                and k[3] == self.find_ci and k[4] == e.row and k[5] == e.col):
            return k[6]                 # nothing moved: the last answer, nothing made
        got = self._counted()
        self._ck = (e, e.edits, self.find_q, self.find_ci, e.row, e.col, got)
        return got

    def _counted(self):
        ci = self.find_ci
        q = self.find_q.lower() if ci else self.find_q
        if not q:
            return 0, 0
        cur = n = 0
        for r, ln in enumerate(self.ed.lines):
            if ci:
                ln = ln.lower()
            at = ln.find(q)
            while at >= 0:
                n += 1
                if (r, at) <= (self.ed.row, self.ed.col):
                    cur = n
                at = ln.find(q, at + 1)
        return (cur or (1 if n else 0)), n

    @property
    def match(self):
        return self._count()[0]

    @property
    def matches(self):
        return self._count()[1]

    def summary(self):
        """A write as the trace shows it: the caret, the selection, the lines."""
        e = self.ed
        return "L%d:%d%s %dl" % (e.row + 1, e.col + 1,
                                 " sel%d" % self.sel if e.has_selection() else "", len(e.lines))

    # -- what a handler reads -----------------------------------------------

    def text(self):
        return self.ed.text()

    def defs(self):
        """(name, line from 1) of each definition."""
        return [(n, r + 1) for n, r in self.ed.def_symbols()]

    def completions(self, names):
        return self.ed.completions(names)

    def count(self):
        return self._count()

    # -- the verbs (a handler's, through DocView; the node's keys) ----------

    def _open(self):
        if self.journal is not None:
            self.journal.open()
        elif self._burst is None:
            self._burst = self.ed.text()

    def _changed(self, got=True):
        if got and self.journal is not None:
            self.journal.edited()
        self.gen += 1
        return got

    def close_burst(self):
        if self.journal is not None:
            self.journal.close()
            return
        before, self._burst = self._burst, None
        if before is not None and before != self.ed.text():
            op = text_diff_op(before, self.ed.text())
            if op is not None:
                self.history.record(op)

    def key(self, code):
        """One typed byte from the node: True where the text changed."""
        self._open()
        got = self.ed.key(code)
        self._changed(got)
        if code in (0x0D, 0x0A) or (self.journal is None and 0x20 <= code <= 0x7E
                                    and chr(code) in _BREAKS):
            self.close_burst()
        return got

    def move(self, dy, dx, select=None):
        self.ed.move(dy, dx, select)
        self.gen += 1

    def place(self, line, col, select=False):
        e = self.ed
        e.row = max(0, min(len(e.lines) - 1, line - 1))
        e.col = max(0, min(len(e.lines[e.row]), col - 1))
        if not select:
            e.sel = None
        e._scroll()
        self.gen += 1

    def set_text(self, text):
        self.close_burst()
        self.ed.set_text(text)
        if self.history is not None:
            self.history.clear()
        self._changed()

    def insert(self, text):
        self._open()
        self.ed.insert_text(text)
        self._changed()
        self.close_burst()

    def go(self, line, col=1):
        self.ed.goto_row(line - 1, col - 1)
        self.gen += 1

    def select(self, line, col, line2, col2):
        self.ed.goto_row(line2 - 1, col2 - 1)
        self.ed.sel = (line - 1, col - 1)
        self.gen += 1

    def find(self, query, forward=True, here=False, case=None):
        """The next match of `query` from the caret (`here`: one starting at
        it taken); `case` True matches case, False ignores it, None keeps
        the last find's."""
        if case is not None:
            self.find_ci = not case
        if self.journal is None:
            self.close_burst()
        self.find_q = query
        got = self.ed.find(query, forward, self.find_ci, include_current=here)
        self.gen += 1
        return got

    def undo(self):
        if self.journal is not None:
            got = self.journal.undo()
        else:
            self.close_burst()
            got = self.history.undo() is not None
        self.gen += 1
        return got

    def redo(self):
        if self.journal is not None:
            got = self.journal.redo()
        else:
            got = self.history.redo() is not None
        self.gen += 1
        return got

    def complete(self, word):
        self._open()
        self.ed.complete(word)
        self._changed()
        if self.journal is None:
            self.close_burst()

    def indent(self):
        self._open()
        self.ed.indent_selection()
        self._changed()
        if self.journal is None:
            self.close_burst()

    def outdent(self):
        self._open()
        got = self.ed.outdent_selection()
        self._changed(got)
        if self.journal is None:
            self.close_burst()
        return got

    def copy(self):
        return self.ed.copy()

    def cut(self):
        self._open()
        got = self.ed.cut()
        self._changed(got)
        if self.journal is None:
            self.close_burst()
        return got

    def paste(self):
        self._open()
        got = self.ed.paste()
        self._changed(got)
        if self.journal is None:
            self.close_burst()
        return got

    def select_all(self):
        self.ed.select_all()
        self.gen += 1

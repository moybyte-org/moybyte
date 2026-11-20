"""The engine's side of a `ui.doc`: today's text machinery under one object.

The buffer, caret, selection, find, completion and Go to are the Code tab's
`CodeEditor` (runtime/editors/editors_code.py); undo is the op journal the
cart editor handle keeps (runtime/editors/op_history.py, a burst closed at
Enter, punctuation and an undo press). Nothing here is the app's: a handler
reaches it through `uitree.DocView`, a template through `uitree.DOC_FIELDS`,
and the text editor node feeds it typed keys and draws it.

Lines and columns cross the boundary from 1; `CodeEditor` counts from 0."""
from editors_code import CodeEditor
from op_history import History, TextEditCodec, text_diff_op

_BREAKS = ".,;:"


class DocBuf:
    def __init__(self, text, lang):
        self.lang = lang
        self.ed = CodeEditor(text, 80, 40)
        self.history = History(self.ed, TextEditCodec())
        self._burst = None
        self.find_q = ""          # the query the node's find= names, this frame
        self.marks = ()           # (line from 1, kind, words), the node's marks= model
        self.gen = 0              # moves on every change the view draws

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
        return self.history.can_undo() or (self._burst is not None and self._burst != self.ed.text())

    @property
    def can_redo(self):
        return self.history.can_redo()

    @property
    def word(self):
        return self.ed.word_prefix()

    def _count(self):
        q = self.find_q.lower()
        if not q:
            return 0, 0
        cur = n = 0
        for r, ln in enumerate(self.ed.lines):
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
        if self._burst is None:
            self._burst = self.ed.text()

    def close_burst(self):
        before, self._burst = self._burst, None
        if before is not None and before != self.ed.text():
            op = text_diff_op(before, self.ed.text())
            if op is not None:
                self.history.record(op)

    def key(self, code):
        """One typed byte from the node: True where the text changed."""
        self._open()
        got = self.ed.key(code)
        if code in (0x0D, 0x0A) or (0x20 <= code <= 0x7E and chr(code) in _BREAKS):
            self.close_burst()
        self.gen += 1
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
        self.history.clear()
        self.gen += 1

    def insert(self, text):
        self._open()
        self.ed.insert_text(text)
        self.close_burst()
        self.gen += 1

    def go(self, line, col=1):
        self.ed.goto_row(line - 1, col - 1)
        self.gen += 1

    def select(self, line, col, line2, col2):
        self.ed.goto_row(line2 - 1, col2 - 1)
        self.ed.sel = (line - 1, col - 1)
        self.gen += 1

    def find(self, query, forward=True, here=False):
        self.close_burst()
        self.find_q = query
        got = self.ed.find(query, forward, True, include_current=here)
        self.gen += 1
        return got

    def undo(self):
        self.close_burst()
        got = self.history.undo() is not None
        self.gen += 1
        return got

    def redo(self):
        got = self.history.redo() is not None
        self.gen += 1
        return got

    def complete(self, word):
        self._open()
        self.ed.complete(word)
        self.close_burst()
        self.gen += 1

    def indent(self):
        self._open()
        self.ed.indent_selection()
        self.close_burst()
        self.gen += 1

    def outdent(self):
        self._open()
        self.ed.outdent_selection()
        self.close_burst()
        self.gen += 1

    def copy(self):
        return self.ed.copy()

    def cut(self):
        self._open()
        got = self.ed.cut()
        self.close_burst()
        self.gen += 1
        return got

    def paste(self):
        self._open()
        got = self.ed.paste()
        self.close_burst()
        self.gen += 1
        return got

    def select_all(self):
        self.ed.select_all()
        self.gen += 1

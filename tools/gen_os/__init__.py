"""tools/gen_os_table.py's generator: the call table's source, its rules and
one emitter per tier."""


class TableError(ValueError):
    """A source the rules refuse, named by its row."""

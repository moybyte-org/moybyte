"""The terminal's cell grid (native/moy_sh/moy_term.h), through the screen
face's host binding (runtime/host/moy_sh.py): what the VT100 subset a line
program and a full-screen app write leaves on the grid, as a view reads it."""

from array import array

import pytest

from runtime import native_build

if native_build.cc() is None:
    pytest.skip("no C compiler", allow_module_level=True)

from runtime import moy_sh  # noqa: E402


@pytest.fixture
def term(tmp_path):
    made = []

    def make(cols=20, rows=5):
        s = moy_sh.Screen(cols, rows, str(tmp_path))
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def view(s):
    v = array("i", [0, 0, 0])
    s.take(v)
    return [s.line(i) for i in range(v[1])]


def cursor(s):
    c = array("i", [0, 0, 0])
    s.cursor(c)
    return tuple(c)


def test_lines_print_as_a_log_and_wrap_at_the_width(term):
    s = term(10, 4)
    assert view(s) == [""]
    s.write("one\ntwo\n")
    assert view(s) == ["one", "two", ""] and cursor(s) == (2, 0, 1)
    s.write("0123456789abc")
    assert view(s)[2:] == ["0123456789", "abc"]
    s.write("\n0123456789")             # the last column: the wrap waits for more
    assert cursor(s)[1] == 9 and view(s)[-1] == "0123456789"


def test_lines_scrolled_off_the_screen_are_the_scrollback(term):
    s = term(10, 3)
    s.write("".join("l%d\n" % i for i in range(6)))
    assert view(s) == ["l%d" % i for i in range(6)] + [""]
    s.write("\x1b[3J")                  # the scrollback goes, the screen stays
    assert view(s) == ["l4", "l5", ""]


def test_the_ring_drops_its_oldest_line_and_take_names_what_changed(term):
    s = term(8, 2)
    s.write("".join("%d\n" % i for i in range(250)))
    v = array("i", [0, 0, 0])
    s.take(v)
    assert v[0] > 0 and s.line(0) == str(v[0]) and v[2] == v[0]
    s.take(v)
    assert v[2] == v[0] + v[1]          # nothing since
    s.write("x")
    s.take(v)
    assert v[2] == v[0] + v[1] - 1 and s.line(v[1] - 1) == "x"


def test_the_cursor_moves_and_erases(term):
    s = term(10, 4)
    s.write("abcdef\x1b[3D!\x1b[K")
    assert view(s) == ["abc!"]
    s.write("\x1b[2;5Hx\x1b[1;1H\x1b[2C?")
    assert view(s) == ["ab?!", "    x"]
    s.write("\x1b[2;6H\x1b[1K")
    assert view(s)[1] == ""
    s.write("\x1b[2;5Hx")
    s.write("\x1b[2;1H\x1b[2K\x1b[1;2H\x1b[0J")
    assert view(s) == ["a", ""]
    s.write("\x1b[s\x1b[4;4Hz\x1b[uy")
    assert view(s) == ["ay", "", "", "   z"]


def test_clear_empties_the_view(term):
    s = term(10, 4)
    s.write("a\nb\nc\nd\ne\n")
    s.write("\x1b[H\x1b[2J\x1b[3J")
    assert view(s) == [""] and cursor(s)[:2] == (0, 0)


def test_sgr_inks_become_the_escapes_a_text_view_maps(term):
    s = term(30, 3)
    s.write("\x1b[31mred\x1b[0m \x1b[1;92mok\x1b[39m \x1b[2mdim\x1b[22m n \x1b[38;5;4mb\x1b[m")
    # a blank carries no ink: the change is said at the next glyph
    assert view(s) == ["\x1b[31mred \x1b[92mok \x1b[2mdim \x1b[0mn \x1b[34mb\x1b[0m"]


def test_rgb_inks_and_grounds_are_the_base_colours_exactly(term):
    """38;2 and 48;2 take the nearest of the console's sixteen base colours
    and say it back as that colour's RGB; a ground on a blank is kept, and a
    half-block's cell carries both."""
    s = term(20, 3)
    s.write("\x1b[38;2;131;118;156;48;2;29;43;83m\u2580\x1b[38;2;255;0;80m\u2584"
            "\x1b[0m.\x1b[48;5;8m  \x1b[49mx\x1b[0m\n")
    assert view(s)[0] == ("\x1b[38;2;131;118;156m\x1b[48;2;29;43;83m\u2580"
                          "\x1b[38;2;255;0;77m\u2584\x1b[0m.\x1b[48;2;255;0;77m  "
                          "\x1b[49mx")
    s.write("\x1b[38;5;1mred\x1b[38;2;0;0;0m\x1b[0mplain\n")
    assert view(s)[1] == "\x1b[31mred\x1b[0mplain"


def test_utf8_tabs_and_unknown_escapes(term):
    s = term(20, 3)
    s.write("é→".encode("utf-8") + b"\xff|")
    assert view(s) == ["é→�|"]
    s.write("\na\tb\x1b[?1049h\x1b(Bc\x1b[?25l")
    assert view(s)[1] == "a       bc" and cursor(s)[2] == 0
    s.write("\x1b[?25h")
    assert cursor(s)[2] == 1


def test_a_resize_keeps_each_lines_cells_and_the_cursors_line(term):
    s = term(10, 4)
    s.write("0123456789\nab\n")
    assert s.resize(4, 6)
    assert view(s) == ["0123", "ab", ""] and cursor(s) == (2, 0, 1)
    assert s.resize(12, 2)
    assert view(s) == ["0123", "ab", ""] and cursor(s)[0] == 2
    s.write("xyz")
    assert view(s)[-1] == "xyz"

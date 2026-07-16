"""Sheets' hand-rolled formula engine (#78): precedence, cell refs, the block
operator vocabulary (#48), cycle detection, and the never-raise contract."""

from runtime import formula as F


def _z(c, r):          # a resolver where every cell is 0
    return 0


# -- cell reference algebra ------------------------------------------------------

def test_column_algebra_round_trips():
    assert F.col_to_index("A") == 0
    assert F.col_to_index("Z") == 25
    assert F.col_to_index("AA") == 26
    assert F.col_to_index("AB") == 27
    for i in (0, 1, 25, 26, 27, 51, 52, 700):
        assert F.col_to_index(F.index_to_col(i)) == i


def test_parse_and_make_ref():
    assert F.parse_ref("A1") == (0, 0)
    assert F.parse_ref("B3") == (1, 2)
    assert F.parse_ref("aa10") == (26, 9)
    assert F.make_ref(1, 2) == "B3"
    assert F.make_ref(0, 0) == "A1"


# -- arithmetic + precedence -----------------------------------------------------

def test_precedence_and_parentheses():
    assert F.eval_formula("=1+2*3", _z) == 7
    assert F.eval_formula("=(1+2)*3", _z) == 9
    assert F.eval_formula("=2*3+4*5", _z) == 26
    assert F.eval_formula("=10-2-3", _z) == 5           # left-associative
    assert F.eval_formula("=20/2/5", _z) == 2
    assert F.eval_formula("=2*(3+(4-1))", _z) == 12


def test_unary_minus_and_leading_equals_optional():
    assert F.eval_formula("=-5+2", _z) == -3
    assert F.eval_formula("=-(2+3)", _z) == -5
    assert F.eval_formula("=--4", _z) == 4
    assert F.eval_formula("3+4", _z) == 7               # '=' is optional


def test_division_returns_float_but_integers_stay_ints():
    assert F.eval_formula("=10/4", _z) == 2.5
    assert isinstance(F.eval_formula("=8/2", _z), int)   # 4, not 4.0
    assert F.eval_formula("=2+3", _z) == 5


# -- the block operator vocabulary (#48) -----------------------------------------

def test_operator_vocabulary_matches_blocks():
    assert F.eval_formula("=mod(10,3)", _z) == 1
    assert F.eval_formula("=mod(0-7,3)", _z) == 2        # floored mod
    assert F.eval_formula("=round(2.7)", _z) == 3
    assert F.eval_formula("=round(2.4)", _z) == 2
    assert F.eval_formula("=abs(0-9)", _z) == 9
    assert F.eval_formula("=min(3,1,2)", _z) == 1
    assert F.eval_formula("=max(3,1,2)", _z) == 3
    assert F.eval_formula("=min(5,2)*max(1,4)", _z) == 8


def test_sum_and_avg_over_scalars():
    assert F.eval_formula("=sum(1,2,3,4)", _z) == 10
    assert F.eval_formula("=avg(2,4,6)", _z) == 4


# -- cell refs + ranges through a Sheet ------------------------------------------

def test_cell_refs_and_dependent_formulas():
    s = F.Sheet("t", 6, 6)
    s.set_cell(0, 0, "5")
    s.set_cell(1, 0, "=A1*2")
    s.set_cell(2, 0, "=A1+B1")
    assert s.value_at(1, 0) == 10
    assert s.value_at(2, 0) == 15
    # An updated dependency recomputes downstream on the next recalc.
    s.set_cell(0, 0, "10")
    assert s.value_at(1, 0) == 20
    assert s.value_at(2, 0) == 30


def test_ranges_for_sum_avg_min_max():
    s = F.Sheet("t", 6, 6)
    for i, v in enumerate((10, 20, 30, 40)):
        s.set_cell(0, i, str(v))            # A1..A4
    res = lambda c, r: s.value_at(c, r)
    assert F.eval_formula("=sum(A1:A4)", res) == 100
    assert F.eval_formula("=avg(A1:A4)", res) == 25
    assert F.eval_formula("=min(A1:A4)", res) == 10
    assert F.eval_formula("=max(A1:A4)", res) == 40


def test_blank_cell_reads_as_zero_in_arithmetic():
    s = F.Sheet("t", 4, 4)
    s.set_cell(0, 0, "=B1+7")               # B1 is blank
    assert s.value_at(0, 0) == 7


def test_text_cell_in_arithmetic_is_an_error_not_a_crash():
    s = F.Sheet("t", 4, 4)
    s.set_cell(0, 0, "hello")
    s.set_cell(1, 0, "=A1+1")
    assert s.value_at(1, 0) == F.ERR


# -- cycle detection -------------------------------------------------------------

def test_direct_and_indirect_cycles_are_LOOP_not_a_hang():
    s = F.Sheet("t", 6, 6)
    s.set_cell(0, 0, "=B1")
    s.set_cell(1, 0, "=A1")                  # A1 <-> B1
    assert s.value_at(0, 0) == F.LOOP
    assert s.value_at(1, 0) == F.LOOP
    # A longer ring: A2 -> B2 -> C2 -> A2.
    s.set_cell(0, 1, "=B2")
    s.set_cell(1, 1, "=C2")
    s.set_cell(2, 1, "=A2")
    assert s.value_at(0, 1) == F.LOOP


def test_self_reference_is_LOOP():
    s = F.Sheet("t", 4, 4)
    s.set_cell(0, 0, "=A1+1")
    assert s.value_at(0, 0) == F.LOOP


# -- the never-raise contract ----------------------------------------------------

def test_malformed_input_never_raises():
    bad = ["=1+", "=*3", "=(1+2", "=)", "=1 2", "=sum(", "=A1:", "=@",
           "==", "=mod(1)", "=round(1,2,3)", "=nope(1)", "=1..2", "=A", "=",
           "=1/0", "=mod(5,0)", "=A1B2", "=1,2", "=()"]
    for src in bad:
        out = F.eval_formula(src, _z)
        assert out in (F.ERR, F.LOOP), (src, out)


def test_literal_parsing():
    assert F.parse_literal("3") == 3
    assert F.parse_literal("-4") == -4
    assert F.parse_literal("2.5") == 2.5
    assert F.parse_literal("hello") == "hello"
    assert F.parse_literal("") == ""
    assert F.parse_literal("3d") == "3d"        # not numeric -> text
    assert F.parse_literal("  7 ") == 7


# -- serialisation round-trip ----------------------------------------------------

def test_sheet_to_dict_stores_formula_and_value():
    s = F.Sheet("wave", 5, 5)
    s.set_cell(0, 0, "5")
    s.set_cell(1, 0, "=A1*2")
    d = s.to_dict()
    assert d["format"] == "moysheet-v1"
    assert d["cells"]["A1"] == {"f": "5", "v": 5}
    assert d["cells"]["B1"] == {"f": "=A1*2", "v": 10}
    back = F.Sheet.from_dict(d)
    assert back.name == "wave"
    assert back.value_at(1, 0) == 10
    assert back.raw_at(1, 0) == "=A1*2"

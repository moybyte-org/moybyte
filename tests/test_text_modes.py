"""The editor MODE table (step 2 of docs/text_editing_2026-09.md): what a
file's name says about how it is edited, and the two surfaces that ask it --
the Editor's Code tab and the shell's text page."""

from pathlib import Path

from runtime import host_app, moy_carts, text_modes


ROOT = Path(__file__).resolve().parent.parent


def _handle(ws, name, mode=None):
    """The shell's text page as a CART reaches it (`runtime/editor_handle.py`)
    -- the table's second consumer since the notebook app was deleted."""
    ctx = ws.app_context("probe", ("files", "clipboard"))
    return ws.player._open_cart_editor(ctx.files, "docs", name, mode,
                                       ws.canvas, ctx.clipboard)


# -- the table ---------------------------------------------------------------

def test_every_extension_lands_on_its_mode():
    assert text_modes.mode_for("note" + moy_carts.DOC_EXT) == text_modes.MD
    assert text_modes.mode_for("main.py") == text_modes.CODE
    assert text_modes.mode_for("main.lua") == text_modes.CODE
    assert text_modes.mode_for("scores.json") == text_modes.JSON
    assert text_modes.mode_for("readme.txt") == text_modes.TEXT
    assert text_modes.mode_for("noextension") == text_modes.TEXT
    assert text_modes.mode_for("MAIN.PY") == text_modes.CODE   # case-blind


def test_markdown_is_the_stores_doc_extension_and_not_a_spelled_string():
    """The table asks moy_carts, so a kind that changes extension keeps its
    mode. Nothing in text_modes.py spells the document extension."""
    src = (ROOT / "runtime" / "text_modes.py").read_text(encoding="utf-8")
    assert '".md"' not in src and "'.md'" not in src
    assert text_modes.BY_EXT[moy_carts.DOC_EXT] == text_modes.MD
    assert text_modes.mode_for_kind("docs", "note") == text_modes.MD
    assert text_modes.file_name("docs", "note") == "note" + moy_carts.DOC_EXT


def test_the_manifest_and_config_are_json_by_name():
    for name in ("manifest.json", "config.json"):
        assert text_modes.mode_for(name) == text_modes.JSON
        assert text_modes.mode_for("/sd/carts/game.moy/" + name) == text_modes.JSON
    # the by-name rule is a table, not two ifs, and it OVERRIDES the extension
    assert set(text_modes.BY_NAME) == {"manifest.json", "config.json"}


def test_a_drawing_is_not_text_and_says_so():
    img = "dragon" + moy_carts.FILE_KINDS["drawings"][0]
    assert text_modes.is_image(img)
    assert not text_modes.is_image("note" + moy_carts.DOC_EXT)
    assert text_modes.is_image(text_modes.file_name("drawings", "dragon"))


def test_prose_wraps_and_code_does_not():
    """PROSE wraps -- markdown and plain text both, because a sentence that
    runs off a 320px screen is unreadable. A document whose LINES mean
    something scrolls sideways instead: breaking a line in code or JSON would
    lie about the file."""
    assert text_modes.MODES[text_modes.MD].wrap is True
    assert text_modes.MODES[text_modes.TEXT].wrap is True
    assert text_modes.MODES[text_modes.CODE].wrap is False
    assert text_modes.MODES[text_modes.JSON].wrap is False
    assert text_modes.mode("story" + moy_carts.DOC_EXT).wrap is True
    assert text_modes.mode("todo.txt").wrap is True


def test_the_language_comes_from_the_runtime_for_a_cart_and_the_extension_alone():
    assert text_modes.cart_lang({"runtime": "lua"}) == "lua"
    assert text_modes.cart_lang({"runtime": "python"}) == "python"
    assert text_modes.cart_lang({}) == "python"
    assert text_modes.cart_lang(None) == "python"
    assert text_modes.lang_for("helper.lua") == "lua"
    assert text_modes.lang_for("helper.py") == "python"


# -- the gates ---------------------------------------------------------------

def test_only_json_and_code_gate_a_save():
    assert text_modes.MODES[text_modes.JSON].gate == "json"
    assert text_modes.MODES[text_modes.CODE].gate == "runtime"
    assert text_modes.MODES[text_modes.MD].gate is None
    assert text_modes.MODES[text_modes.TEXT].gate is None
    # prose never refuses -- there is no such thing as un-parseable prose
    assert text_modes.check(text_modes.MD, "# hi\n{{{") == (True, "")
    assert text_modes.check(text_modes.TEXT, "}}}") == (True, "")


def test_the_json_gate_reports_the_line_in_compile_checks_shape():
    ok, msg = text_modes.check(text_modes.JSON, '{"a": 1}')
    assert (ok, msg) == (True, "")
    ok, msg = text_modes.check(text_modes.JSON, '{\n  "a": 1,\n  oops\n}')
    assert ok is False
    assert msg.startswith("line 3: ")           # compile_check's message shape
    ok, msg = text_modes.check(text_modes.JSON, "")
    assert ok is False and msg


def test_the_code_gate_is_the_carts_runtime_gate():
    py = {"runtime": "python"}
    assert text_modes.check(text_modes.CODE, "x = 1", py) == (True, "")
    ok, msg = text_modes.check(text_modes.CODE, "def (:", py)
    assert ok is False and msg
    # a lua cart has no syntax-only entry on either tier -- it commits (#67)
    assert text_modes.check(text_modes.CODE, "def (:", {"runtime": "lua"}) \
        == (True, "")


# -- the two consumers -------------------------------------------------------

def test_the_code_tab_takes_its_language_from_the_table(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    cart = ws.carts.all[0]
    ws.open_in_editor(cart)
    ws.set_menu_view("code")
    assert ws.code_layer._is_lua() is (text_modes.cart_lang(ws.cart) == "lua")
    ws.cart["runtime"] = "lua"
    assert ws.code_layer._is_lua() is True
    ws.cart["runtime"] = "python"
    assert ws.code_layer._is_lua() is False


def test_a_vault_note_opens_in_markdown_mode(tmp_path):
    carts = str(tmp_path / "carts")
    moy_carts.save_file("docs", "story", "hello", carts)
    ws = host_app.build_workstation(carts)
    ed = _handle(ws, "story")
    assert ed.mode() == text_modes.MD
    assert text_modes.MODES[ed.mode()].wrap is True
    # markdown has no gate: an idle debounce publishes whatever was typed
    ed.set_text("# heading {{{ not json")
    assert ed.save(soft=True)[0] is True
    assert moy_carts.load_file("docs", "story", carts) == "# heading {{{ not json"


def test_an_invalid_json_document_is_refused_softly_and_kept_on_the_way_out(tmp_path):
    carts = str(tmp_path / "carts")
    moy_carts.save_file("docs", "settings", '{"a": 1}', carts)
    ws = host_app.build_workstation(carts)
    ed = _handle(ws, "settings", text_modes.JSON)
    assert ed.mode() == text_modes.JSON

    ed.set_text('{"a": 1,')
    assert ed.save(soft=True)[0] is False             # the debounce refuses
    assert ed.badge().startswith("INVALID")
    assert moy_carts.load_file("docs", "settings", carts) == '{"a": 1}'

    ed.close()                                        # a hard exit writes anyway
    assert moy_carts.load_file("docs", "settings", carts) == '{"a": 1,'
    assert ed.badge().startswith("INVALID")           # and keeps the badge


def test_a_repaired_json_document_clears_the_badge(tmp_path):
    carts = str(tmp_path / "carts")
    moy_carts.save_file("docs", "settings", "{}", carts)
    ws = host_app.build_workstation(carts)
    ed = _handle(ws, "settings", text_modes.JSON)
    ed.set_text("{oops")
    ed.save(soft=True)
    assert ed.badge().startswith("INVALID")
    ed.set_text('{"ok": true}')
    assert ed.save(soft=True)[0] is True
    assert ed.badge() == ""
    assert moy_carts.load_file("docs", "settings", carts) == '{"ok": true}'

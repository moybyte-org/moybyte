"""Storybook (docs/media/look_2026-10/APPS_RESTYLE.md, Storybook): the stories,
a handheld's view-only story and its toast, Present's taps, steps and
transitions, and the desk's editor -- the sorter, a slide's objects, their
edits and undo, a picture copied in from My Art -- all through the app as a
kid drives it, the store under it the user files' `stories` kind."""

import json
from pathlib import Path

from runtime import host_app, moy_carts
from runtime import story_doc as D
from runtime import story_render as R
from runtime.storybook_app import StorybookAppLayer, StorybookLayout, zoom_fit

ROOT = Path(__file__).resolve().parent.parent
_DT = 1 / 30


class _In:
    def __init__(self, *names, key=0):
        self.names = set(names)
        self.last_key = key

    def pressed(self, name):
        return name in self.names


def _frames(ws, n=2):
    for _ in range(n):
        ws._dirty = True
        ws.frame(_DT)


def _key(ws, app, k=0, *names):
    """One press: the key (or buttons) down a frame, then up."""
    app.handle_input(_In(*names, key=k))
    _frames(ws, 1)
    app.handle_input(_In())
    _frames(ws, 1)


def _type(ws, app, text):
    for ch in text:
        _key(ws, app, ord(ch))


def _drawing(w, h, ink=11):
    px = bytearray([1]) * (w * h)
    for y in range(h // 4, h * 3 // 4):
        for x in range(w // 4, w * 3 // 4):
            px[y * w + x] = ink
    return moy_carts.encode_moyimg(w, h, bytes(px))


def _seed(root, stories=(("brave", "The Brave Dragon", 3),)):
    for name, title, n in stories:
        doc = D.new_doc(title)
        for i in range(1, n):
            s = D.layout_slide("blank", 15)
            s["objects"].append(D.text_obj("Slide %d" % (i + 1), 64, 416, 896, 72, 1))
            s["objects"].append(D.shape_obj("bubble", 608, 64, 352, 180, 7, 0, 8, "Hello!",
                                            64, 0, step=1))
            s["transition"] = "fade" if i == 1 else "swipe"
            doc["slides"].append(s)
        moy_carts.save_file("stories", name + "/story.json", D.write(doc), root)


def _handheld(tmp_path, seed=True):
    root = str(tmp_path / "carts")
    ws = host_app.build_workstation(root)
    ws.pointer.visible = False
    if seed:
        _seed(ws.carts_root)
    app = ws._apps_by_id["storybook"]
    ws.open_app(app)
    _frames(ws, 3)
    return ws, app


def _desk(tmp_path, seed=True):
    root = str(tmp_path / "carts")
    ws = host_app.build_workstation(root, sys_size=(1024, 600), panel_diagonal_in=7.0,
                                    windowed=True)
    ws.pointer.visible = False
    if seed:
        _seed(ws.carts_root)
    ws.open_desk()
    app = ws._apps_by_id["storybook"]
    ws.open_app(app)
    _frames(ws, 3)
    return ws, app


def _stored(ws, name):
    return D.read(moy_carts.load_file("stories", name + "/story.json", ws.carts_root))


# -- the app ------------------------------------------------------------------------

def test_storybook_cart_is_versioned_system_app():
    folder = ROOT / "system_carts" / "moybyte.storybook.moy"
    man = json.loads((folder / "manifest.json").read_text())
    assert man["title"] == "Storybook"
    assert man["moybyte"]["system"] is True
    assert "storybook" in man["moybyte"]["permissions"]


def test_storybook_identity_rejects_copies_and_impostors():
    real = {"title": "Storybook", "permissions": ["storybook"],
            "path": "/x/moybyte.storybook.moy"}
    assert StorybookAppLayer.is_app(real)
    assert not StorybookAppLayer.is_app(dict(real, path="/x/storybook_2.moy"))
    assert not StorybookAppLayer.is_app(dict(real, permissions=[]))


def test_only_stories_that_read_are_listed(tmp_path):
    ws, app = _handheld(tmp_path)
    root = ws.carts_root
    deck = {"format": "moydeck-v1", "pages": [{"text": ["Once"], "bg": "dark_blue"}]}
    moy_carts.save_file("stories", "old/story.json", json.dumps(deck), root)
    moy_carts.save_file("stories", "broken/story.json", "{", root)
    moy_carts.save_file("stories", "bare/pic.moyimg", _drawing(8, 8), root)
    app._scan()
    assert [s["name"] for s in app.stories] == ["brave"]


# -- the handheld: stories to look at ----------------------------------------------------

def test_a_handheld_without_stories_says_where_they_are_made(tmp_path):
    ws, app = _handheld(tmp_path, seed=False)
    assert app.stories == [] and not app.layout.desk and app.ed is None
    assert app.page == "stories"


def test_a_handheld_views_a_story_and_does_not_edit_it(tmp_path):
    ws, app = _handheld(tmp_path)
    _key(ws, app, 0, "a")                       # Enter on the focused card opens it
    assert app.page == "view" and app.cur == 0
    assert app.page_title() == "The Brave Dragon"
    _key(ws, app, 0, "right")
    assert app.cur == 1
    _key(ws, app, ord("t"))
    assert app.toast is not None and app.toast[0] == "Stories are edited on a desk"
    _key(ws, app, 127)
    before = moy_carts.load_file("stories", "brave/story.json", ws.carts_root)
    _key(ws, app, 0, "left")
    assert app.cur == 0
    app.close()
    assert moy_carts.load_file("stories", "brave/story.json", ws.carts_root) == before
    assert app.layout.view[2:] == (256, 150), "the story's slide at a quarter"


def test_present_takes_the_glass_and_steps_through_taps(tmp_path):
    ws, app = _handheld(tmp_path)
    _key(ws, app, 0, "a")
    _key(ws, app, ord("p"))
    assert app.show is not None and app.glass
    assert zoom_fit(320, 240) == (5, 16)
    _key(ws, app, 0, "right")                   # slide 2, fading in
    assert app.show["i"] == 1 and app.show["from"] is not None
    _frames(ws, 14)
    assert app.show["from"] is None and app.show["step"] == 0
    _key(ws, app, 0, "right")                   # its bubble, on the tap
    assert app.show["i"] == 1 and app.show["step"] == 1
    _key(ws, app, 0, "right")                   # slide 3, swiping in
    assert app.show["i"] == 2
    _frames(ws, 10)
    _key(ws, app, 0, "left")
    _frames(ws, 10)
    assert app.show["i"] == 1 and app.show["step"] == 1, "back: the slide as it ended"
    _key(ws, app, 27)
    assert app.show is None and not app.glass and app.page == "view" and app.cur == 1


def test_present_draws_no_bar_and_the_bars_band_is_the_slides(tmp_path):
    ws, app = _handheld(tmp_path)
    _key(ws, app, 0, "a")
    _key(ws, app, ord("p"))
    _frames(ws, 2)
    cv = ws.sys_canvas
    top = bytes(cv._buf[:2 * cv.w * 4])
    assert top == bytes(len(top)), "black above the slide, where the bar was"
    ws.pointer.place(14, 9)                     # where the bar's way back was
    ws.pointer.click = True
    ws.handle_pointer()
    ws.pointer.click = False
    assert ws.wm.top_kind() == "storybook" and app.show is not None


# -- the desk: editing ------------------------------------------------------------------------

def test_the_desk_opens_at_its_designed_size(tmp_path):
    ws, app = _desk(tmp_path)
    win = ws.wm._wins["storybook"]
    assert (win.w, win.h) == (900, 536)
    assert app.layout.desk and app.ed is not None


def test_a_new_story_is_written_from_its_title(tmp_path):
    ws, app = _desk(tmp_path, seed=False)
    app._new_story()
    _frames(ws)
    ed = app.ed
    assert app.page == "slide" and ed.writing and ed.chosen == 0
    _type(ws, app, "Moy at sea")
    _key(ws, app, 27)
    assert not ed.writing
    st = app.story
    assert st.title() == "Moy at sea" and st.name == "moy_at_sea"
    app.commit()
    doc = _stored(ws, "moy_at_sea")
    assert doc["title"] == "Moy at sea" and doc["slides"][0]["objects"][0]["text"] == "Moy at sea"


def _edit(tmp_path):
    ws, app = _desk(tmp_path)
    app._open_story(app.stories[0], "deck")
    app.ed.opened()
    app.page = "slide"
    app.cur = 1
    _frames(ws)
    return ws, app, app.ed


def test_the_keys_add_move_and_delete_with_undo(tmp_path):
    ws, app, ed = _edit(tmp_path)
    st = app.story
    first = D.copy(st.slides[1])
    _key(ws, app, ord("t"))                     # a text box, its words to write
    assert ed.writing and len(st.slides[1]["objects"]) == 3
    _type(ws, app, "Hi")
    _key(ws, app, 27)
    o = st.slides[1]["objects"][ed.chosen]
    assert o["text"] == "Hi"
    x = o["x"]
    _key(ws, app, 0, "right")
    assert o["x"] == x + 2
    _key(ws, app, ord("g"))
    _key(ws, app, 0, "right")
    assert o["x"] == x + 18, "G steps 16"
    _key(ws, app, 4)                            # Ctrl+D
    assert len(st.slides[1]["objects"]) == 4
    _key(ws, app, 127)                          # Del
    assert len(st.slides[1]["objects"]) == 3
    assert app.toast is not None and "Ctrl+Z" in app.toast[0]
    for _ in range(8):
        _key(ws, app, 26)                       # Ctrl+Z
    assert st.slides[1] == first
    app.commit()
    assert _stored(ws, "brave")["slides"][1] == first


def test_tab_chooses_in_z_order_and_front_and_back_move_it(tmp_path):
    ws, app, ed = _edit(tmp_path)
    st = app.story
    _key(ws, app, 9)
    assert ed.chosen == 0
    _key(ws, app, ord("]"))
    assert ed.chosen == 1 and st.slides[1]["objects"][1]["type"] == "text"
    _key(ws, app, ord("["))
    assert ed.chosen == 0
    _key(ws, app, 27)
    assert ed.chosen is None
    _key(ws, app, 27)
    assert not ed.on_slide, "Esc with nothing chosen: the keys go to the parts"


def test_a_drag_moves_and_snaps_to_the_slides_middle(tmp_path):
    ws, app, ed = _edit(tmp_path)
    st = app.story
    o = st.slides[1]["objects"][1]              # the bubble at 608, 64, 352 wide
    ed.chosen = 1
    sx, sy = app.layout.slide[0], app.layout.slide[1]
    n, d = app.layout.edit_zoom
    px, py = sx + 700 * n // d, sy + 100 * n // d
    assert ed.handle_pointer(px, py, True, True)
    # left to two screen pixels short of the bubble's left edge on the middle
    to = px - (608 - 512) * n // d + 2
    ed.handle_pointer(to, py, False, True)
    assert o["x"] == 512 and ed.guides and ed.guides[0] == ("v", 512)
    ed.handle_pointer(to, py, False, False)
    assert ed.guides == () and len(st.undos) == 1
    st.undo()
    assert st.slides[1]["objects"][1]["x"] == 608


def test_the_sorter_adds_moves_and_deletes_slides(tmp_path):
    ws, app = _desk(tmp_path)
    app._open_story(app.stories[0], "deck")
    app.ed.opened()
    _frames(ws)
    st = app.story
    app._focus = "Slide 1"
    _frames(ws)
    _key(ws, app, ord("n"))
    assert len(st.slides) == 4 and app.cur == 2
    assert st.slides[2]["objects"][0]["image"] is None, "Picture: the last layout used"
    _frames(ws)
    _key(ws, app, ord("]"))
    assert st.slides[3]["objects"][0].get("image", 1) is None
    _key(ws, app, 127)
    assert len(st.slides) == 3
    for _ in range(3):
        _key(ws, app, 26)
    assert len(st.slides) == 3 and [len(s["objects"]) for s in st.slides] == [2, 2, 2]


def test_a_picture_from_my_art_is_copied_in_and_fills_its_frame(tmp_path):
    ws, app = _desk(tmp_path)
    root = ws.carts_root
    moy_carts.save_file("drawings", "dragon", _drawing(1024, 600), root)
    app._open_story(app.stories[0], "deck")
    ed = app.ed
    ed.opened()
    j = ed._new_slide(0, "two")
    app.page = "slide"
    ed.chosen = 0
    _key(ws, app, 13)                           # Enter on an empty frame
    assert app.page == "pictures"
    _frames(ws)
    ed._place(("dragon", 1024, 600))
    st = app.story
    o = st.slides[j]["objects"][0]
    assert o["image"] == "dragon" and o["scale"] == 0.5 and (o["x"], o["y"]) == (0, 40)
    assert ed.chosen == 1, "the next empty frame is chosen"
    item = moy_carts.file_path("stories", st.name, root)
    assert Path(item, "dragon.moyimg").read_text() == moy_carts.load_file("drawings", "dragon",
                                                                          root)
    half = moy_carts.decode_moyimg(Path(item, "dragon_h.moyimg").read_text())
    assert half[:2] == (512, 300)
    ed._place(("dragon", 1024, 600))
    assert st.slides[j]["objects"][1]["image"] == "dragon_2", "a second copy, its own name"
    app.commit()
    assert _stored(ws, st.name)["slides"][j]["objects"][0]["image"] == "dragon"


def test_the_full_screen_window_edits_at_five_eighths(tmp_path):
    host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                               panel_diagonal_in=7.0, windowed=True)
    lay = StorybookLayout(1024, 600, 1, True)
    assert lay.full and lay.edit_zoom == (5, 8) and lay.slide[2:] == (640, 375)
    lay = StorybookLayout(900, 492, 1, True)
    assert not lay.full and lay.edit_zoom == (1, 2) and lay.slide[2:] == (512, 300)
    assert R.size(5, 64) == (80, 47)


def test_a_drawing_put_in_a_story_is_its_new_last_slide_and_undo_takes_it_back(tmp_path):
    """Paint's Put in a story (APPS_RESTYLE.md, Paint): the drawing becomes a
    new last slide's background, copied in; the toast's Undo takes it back."""
    ws, app = _handheld(tmp_path)
    root = ws.carts_root
    moy_carts.save_file("drawings", "garden", _drawing(320, 240), root)
    files = app._files
    i, name, title = D.put_drawing(files, "brave", "garden")
    assert (i, name, title) == (3, "garden", "The Brave Dragon")
    doc = _stored(ws, "brave")
    assert doc["slides"][3] == {"bg": "garden", "transition": "none", "objects": []}
    assert moy_carts.load_file("stories", "brave/garden.moyimg", root) == \
        moy_carts.load_file("drawings", "garden", root)
    assert D.put_drawing(files, "brave", "garden")[1] == "garden_2"
    assert D.take_back(files, "brave", 4, "garden_2")
    assert D.take_back(files, "brave", 3, "garden")
    assert len(_stored(ws, "brave")["slides"]) == 3
    assert moy_carts.load_file("stories", "brave/garden.moyimg", root) is None
    assert D.put_drawing(files, "nope", "garden")[0] is None


def test_paints_door_lists_the_stories_and_adds_a_slide_with_its_undo(tmp_path):
    from runtime import storybook_app as sb
    ws, app = _handheld(tmp_path)
    moy_carts.save_file("drawings", "garden", _drawing(320, 240), ws.carts_root)
    rows = sb.story_list(None, app._files)
    assert rows == [{"title": "The Brave Dragon", "folder": "brave", "slides": 3,
                     "cover": None}]
    assert sb.story_list(app._files) == rows
    title, n, undo = sb.add_picture_slide(None, app._files, rows[0], "garden")
    assert (title, n) == ("The Brave Dragon", 4)
    assert len(_stored(ws, "brave")["slides"]) == 4
    assert undo()
    assert len(_stored(ws, "brave")["slides"]) == 3
    assert sb.add_picture_slide(None, app._files, rows[0], "nope")[1] == 0


def _tap_part(ws, app, part):
    cx, cy, _cw, _ch = ws.wm._wins["storybook"].content_rect()
    x, y, w, h = app._ui.box(part)
    ws.pointer.place(cx + x + w // 2, cy + y + h // 2)
    ws.pointer.click = True
    ws.handle_pointer()
    _frames(ws, 1)
    ws.pointer.click = False
    ws.handle_pointer()
    _frames(ws, 2)


def test_the_sorters_new_slide_card_opens_the_layouts(tmp_path):
    ws, app = _desk(tmp_path)
    app._open_story(app.stories[0], "deck")
    app.ed.opened()
    _frames(ws)
    _tap_part(ws, app, "Add slide")
    assert app.ed.menu == "layouts"


def test_deleting_the_last_slide_from_the_sorters_button(tmp_path):
    ws, app = _desk(tmp_path)
    app._open_story(app.stories[0], "deck")
    app.ed.opened()
    _frames(ws)
    n = len(app.story.slides)
    app._focus = "Slide %d" % (n - 1)
    _frames(ws)
    _tap_part(ws, app, "Delete")
    assert len(app.story.slides) == n - 1
    _frames(ws)

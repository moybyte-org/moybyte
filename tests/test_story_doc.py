"""A story's document (runtime/story_doc.py): the moystory-2 reader takes its
format and nothing else, and the edits record what they change so undo and
redo give it back."""

import json

import pytest

from runtime import story_doc as D

# docs/media/look_2026-10/APPS_RESTYLE.md's example, as it is written there.
EXAMPLE = """{"format": "moystory-2", "slides": [{"bg": 15, "transition": "fade", "objects": [
 {"type": "picture", "x": 64, "y": 64, "image": "pg2", "scale": 0.5, "clear": false, "step": 0},
 {"type": "shape", "shape": "bubble", "x": 608, "y": 64, "w": 352, "h": 180, "flip": 0,
  "fill": 7, "line": 0, "width": 8, "text": "Hello!", "font": "sans_bold", "size": 64,
  "colour": 0, "align": "centre", "step": 1},
 {"type": "text", "x": 64, "y": 416, "w": 896, "text": "Once upon a time...",
  "font": "sans_bold", "size": 72, "colour": 1, "align": "centre", "strip": null, "step": 0},
 {"type": "sprite", "x": 640, "y": 300, "tile": 3, "tw": 2, "th": 2, "scale": 6, "step": 0}]}]}"""


def _doc():
    return json.loads(EXAMPLE)


def test_the_documented_example_reads():
    doc = D.read(EXAMPLE)
    assert doc is not None
    assert [o["type"] for o in doc["slides"][0]["objects"]] == ["picture", "shape", "text",
                                                               "sprite"]
    assert D.images_of(doc) == ["pg2"]
    assert D.read(D.write(dict(doc, title="The Brave Dragon")))["title"] == "The Brave Dragon"


def test_an_old_story_carts_deck_is_not_a_story():
    deck = {"format": "moydeck-v1", "pages": [{"text": ["Once"], "bg": "dark_blue"}]}
    assert D.read(json.dumps(deck)) is None
    assert D.read("not json") is None
    assert D.read("[]") is None


@pytest.mark.parametrize("break_it", [
    lambda d: d.update(format="moystory-1"),
    lambda d: d.update(extra=1),
    lambda d: d.update(slides=[]),
    lambda d: d.update(slides=d["slides"] * 17),
    lambda d: d.update(title="x" * (D.MAX_TITLE + 1)),
    lambda d: d["slides"][0].update(bg=16),
    lambda d: d["slides"][0].update(bg="Not a name"),
    lambda d: d["slides"][0].update(transition="wipe"),
    lambda d: d["slides"][0].update(notes=""),
    lambda d: d["slides"][0]["objects"].extend([d["slides"][0]["objects"][2]] * 9),
    lambda d: d["slides"][0]["objects"][0].update(scale=0.25),
    lambda d: d["slides"][0]["objects"][0].update(scale=True),
    lambda d: d["slides"][0]["objects"][0].update(clear=0),
    lambda d: d["slides"][0]["objects"][0].pop("step"),
    lambda d: d["slides"][0]["objects"][1].update(shape="heart"),
    lambda d: d["slides"][0]["objects"][1].update(width=6),
    lambda d: d["slides"][0]["objects"][1].update(fill=16),
    lambda d: d["slides"][0]["objects"][2].update(text="x" * (D.MAX_TEXT + 1)),
    lambda d: d["slides"][0]["objects"][2].update(font="comic"),
    lambda d: d["slides"][0]["objects"][2].update(size=4),
    lambda d: d["slides"][0]["objects"][2].update(x=1.5),
    lambda d: d["slides"][0]["objects"][2].update(x=True),
    lambda d: d["slides"][0]["objects"][2].update(align="center"),
    lambda d: d["slides"][0]["objects"][3].update(type="video"),
])
def test_a_document_off_its_format_reads_as_absent(break_it):
    d = _doc()
    break_it(d)
    assert D.read(json.dumps(d)) is None


def test_too_many_images_reads_as_absent():
    d = _doc()
    slides = []
    for i in range(13):
        s = {"bg": "bg%d" % i, "transition": "none",
             "objects": [D.picture_obj("p%d" % i, 0, 0)]}
        slides.append(s)
    d["slides"] = slides[:12]
    assert D.read(json.dumps(d)) is not None          # 24 images
    d["slides"] = slides
    assert D.read(json.dumps(d)) is None              # 26


def test_a_layouts_empty_frame_reads_with_its_box():
    d = {"format": D.FORMAT, "title": "", "slides": [D.layout_slide("two", 1)]}
    assert D.read(json.dumps(d)) is not None
    del d["slides"][0]["objects"][0]["w"]
    assert D.read(json.dumps(d)) is None


def test_a_new_story_is_one_title_slide():
    d = D.new_doc("Moy goes to sea")
    assert D.read(D.write(d)) == d
    assert len(d["slides"]) == 1 and d["slides"][0]["objects"][0]["text"] == "Moy goes to sea"


def test_edits_undo_and_redo_to_the_same_document():
    st = D.Story("brave", D.read(EXAMPLE))
    first = D.copy(st.doc)
    st.set(0, 2, "text", "Long ago...")
    j = st.add(0, D.text_obj("The end", 64, 500, 896, 48, 7))
    st.place(0, j, 100, 520, hold=False)
    st.to_back(0, j)
    k = st.add_slide(0, "picture")
    st.set_slide(k, "transition", "swipe")
    st.move_slide(k, -1)
    st.delete_slide(0)
    edited = D.copy(st.doc)
    assert D.read(D.write(edited)) is not None
    while st.can_undo():
        st.undo()
    assert st.doc == first and st.slides is st.doc["slides"]
    while st.can_redo():
        st.redo()
    assert st.doc == edited


def test_a_drag_is_one_undo_step():
    st = D.Story("brave", D.read(EXAMPLE))
    for x in range(70, 120, 5):
        st.place(0, 2, x, 416)
    st.end()
    st.place(0, 2, 300, 416)
    st.end()
    assert len(st.undos) == 2
    st.undo()
    st.undo()
    assert st.objects(0)[2]["x"] == 64


def test_the_limits_hold():
    st = D.Story("s", D.new_doc(""))
    for _ in range(D.MAX_SLIDES - 1):
        assert st.add_slide(0, "blank") is not None
    assert st.add_slide(0, "blank") is None
    for _ in range(D.MAX_OBJECTS - 2):
        assert st.add(0, D.text_obj("x", 0, 0, 10, 32, 7)) is not None
    assert st.add(0, D.text_obj("x", 0, 0, 10, 32, 7)) is None
    while len(st.slides) > 1:
        st.delete_slide(0)
    assert st.delete_slide(0) is None, "a story keeps one slide"

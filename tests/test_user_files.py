"""The #108 user-files layer: files/<kind>/ beside the carts dir -- the kind
registry, list/load/save/rename/duplicate verbs, the restorable trash, and the
one-shot artwork.moyimg move. Same shared runtime/moy_carts.py the device
freezes."""

import json
import os
from pathlib import Path

from runtime import moy_carts  # noqa: E402

import pytest  # noqa: E402


def _root(tmp_path):
    return str(tmp_path / "carts")


def test_unknown_kind_is_a_loud_error(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(ValueError):
        moy_carts.list_files("selfies", root)
    with pytest.raises(ValueError):
        moy_carts.save_file("selfies", "a", "x", root)


def test_files_root_is_a_sibling_of_the_carts_dir(tmp_path):
    root = _root(tmp_path)
    assert moy_carts.files_root(root) == str(tmp_path / "files")
    assert moy_carts.file_path("drawings", "dragon", root) == str(
        tmp_path / "files" / "drawings" / "dragon.moyimg")


def test_save_load_roundtrip_and_listing(tmp_path):
    root = _root(tmp_path)
    assert moy_carts.list_files("drawings", root) == []
    assert moy_carts.load_file("drawings", "dragon", root) is None
    stored = moy_carts.save_file("drawings", "Dragon Art", "BLOB", root)
    assert stored == "dragon_art"            # kid titles slug to filenames
    assert moy_carts.load_file("drawings", "dragon_art", root) == "BLOB"
    assert moy_carts.list_files("drawings", root) == ["dragon_art"]


def test_listing_is_newest_first(tmp_path):
    root = _root(tmp_path)
    for i, name in enumerate(("old", "mid", "new")):
        moy_carts.save_file("drawings", name, name, root)
        os.utime(moy_carts.file_path("drawings", name, root), (1000 + i, 1000 + i))
    assert moy_carts.list_files("drawings", root) == ["new", "mid", "old"]


def test_listing_skips_atomic_write_orphans(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("drawings", "keep", "x", root)
    d = moy_carts.file_kind_dir("drawings", root)
    for orphan in ("keep.moyimg.bak", "keep.moyimg.tmp", ".moyimg", "notes.txt"):
        with open(d + "/" + orphan, "w") as f:
            f.write("junk")
    assert moy_carts.list_files("drawings", root) == ["keep"]


def test_auto_names_never_collide(tmp_path):
    root = _root(tmp_path)
    assert moy_carts.new_file_name("drawings", root) == "drawing_1"
    moy_carts.save_file("drawings", "drawing_1", "a", root)
    assert moy_carts.new_file_name("drawings", root) == "drawing_2"
    moy_carts.save_file("drawings", "drawing_2", "b", root)
    moy_carts.delete_file("drawings", "drawing_1", root)
    # drawing_1 is free again after a delete; numbering fills the gap.
    assert moy_carts.new_file_name("drawings", root) == "drawing_1"


def test_rename_slugs_and_uniquifies(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("drawings", "drawing_1", "a", root)
    moy_carts.save_file("drawings", "castle", "b", root)
    assert moy_carts.rename_file("drawings", "drawing_1", "My Castle!", root) == "my_castle"
    assert moy_carts.load_file("drawings", "my_castle", root) == "a"
    # Renaming onto an existing name never clobbers it.
    assert moy_carts.rename_file("drawings", "my_castle", "castle", root) == "castle_2"
    assert moy_carts.load_file("drawings", "castle", root) == "b"
    # A blank or unchanged title is a no-op.
    assert moy_carts.rename_file("drawings", "castle", "  !!", root) == "castle"


def test_duplicate_numbers_upward(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("drawings", "cat", "MEOW", root)
    assert moy_carts.duplicate_file("drawings", "cat", root) == "cat_2"
    assert moy_carts.duplicate_file("drawings", "cat", root) == "cat_3"
    assert moy_carts.load_file("drawings", "cat_3", root) == "MEOW"


def test_delete_moves_to_trash_and_restore_comes_back(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("drawings", "cat", "MEOW", root)
    assert moy_carts.delete_file("drawings", "cat", root) == "cat"
    assert moy_carts.list_files("drawings", root) == []
    assert moy_carts.trash_list(root) == [("drawings", "cat")]
    assert moy_carts.restore_file("drawings", "cat", root) == "cat"
    assert moy_carts.load_file("drawings", "cat", root) == "MEOW"
    assert moy_carts.trash_list(root) == []


def test_trash_name_collisions_uniquify_both_ways(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("drawings", "cat", "one", root)
    moy_carts.delete_file("drawings", "cat", root)
    moy_carts.save_file("drawings", "cat", "two", root)
    assert moy_carts.delete_file("drawings", "cat", root) == "cat_2"
    assert sorted(moy_carts.trash_list(root)) == [
        ("drawings", "cat"), ("drawings", "cat_2")]
    moy_carts.save_file("drawings", "cat", "three", root)
    # Restoring while a new "cat" exists lands beside it, never over it.
    assert moy_carts.restore_file("drawings", "cat", root) == "cat_2"
    assert moy_carts.load_file("drawings", "cat", root) == "three"
    assert moy_carts.load_file("drawings", "cat_2", root) == "one"


def test_empty_trash_and_count_prune(tmp_path):
    root = _root(tmp_path)
    for i in range(3):
        name = "d" + str(i)
        moy_carts.save_file("drawings", name, name, root)
        moy_carts.delete_file("drawings", name, root)
    moy_carts.empty_trash(root)
    assert moy_carts.trash_list(root) == []
    # Prune keeps only the newest `keep` entries.
    for i in range(5):
        name = "p" + str(i)
        moy_carts.save_file("drawings", name, name, root)
        moy_carts.delete_file("drawings", name, root)
        p = moy_carts._trash_path("drawings", name, root)
        os.utime(p, (1000 + i, 1000 + i))
    moy_carts.prune_trash(root, keep=2)
    assert moy_carts.trash_list(root) == [("drawings", "p4"), ("drawings", "p3")]


def test_folder_valued_recordings_ride_the_same_verbs(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(ValueError):
        moy_carts.save_file("recordings", "alphabet", "x", root)
    d = moy_carts.file_path("recordings", "alphabet", root)
    os.makedirs(d)
    with open(d + "/pack.json", "w") as f:
        f.write("{}")
    with open(d + "/000.pcm", "w") as f:
        f.write("pcm")
    assert moy_carts.list_files("recordings", root) == ["alphabet"]
    assert moy_carts.load_file("recordings", "alphabet", root) is None
    assert moy_carts.duplicate_file("recordings", "alphabet", root) == "alphabet_2"
    assert os.path.exists(
        moy_carts.file_path("recordings", "alphabet_2", root) + "/000.pcm")
    moy_carts.delete_file("recordings", "alphabet", root)
    assert moy_carts.trash_list(root) == [("recordings", "alphabet")]
    assert moy_carts.restore_file("recordings", "alphabet", root) == "alphabet"
    assert os.path.exists(d + "/pack.json")
    moy_carts.delete_file("recordings", "alphabet_2", root)
    moy_carts.empty_trash(root)                  # folder entries fully removed
    assert moy_carts.trash_list(root) == []


# -- provenance stamps (#108 phase 2) --------------------------------------------

def test_provenance_stamp_roundtrips_and_is_ignored_by_decoders():
    import json
    blob = moy_carts.encode_moyimg(2, 2, bytes((5, 6, 7, 8)))
    sig = moy_carts.content_sig(blob)
    stamped = moy_carts.stamp_provenance(blob, "drawings", "dragon", sig)
    # The stamp adds src/sig but leaves the pixels intact for the image decoder.
    assert moy_carts.read_provenance(stamped) == ("drawings/dragon", sig)
    assert moy_carts.decode_moyimg(stamped) == moy_carts.decode_moyimg(blob)
    assert json.loads(stamped)["src"] == "drawings/dragon"


def test_read_provenance_absent_or_garbage_is_none():
    plain = moy_carts.encode_moyimg(1, 1, bytes((3,)))
    assert moy_carts.read_provenance(plain) == (None, None)
    for bad in ("", "not json", "[]", None):
        assert moy_carts.read_provenance(bad) == (None, None)


def test_content_sig_changes_when_the_blob_changes():
    a = moy_carts.encode_moyimg(2, 2, bytes((1, 1, 1, 1)))
    b = moy_carts.encode_moyimg(2, 2, bytes((1, 1, 1, 2)))
    assert moy_carts.content_sig(a) != moy_carts.content_sig(b)
    assert moy_carts.content_sig("") == 0


# -- documents are plain Markdown (2026-09-07) -------------------------------

WRAPPED = '{"format": "moytext-v1", "body": "line one\\nline two"}'


def _docs_dir(root):
    return Path(moy_carts.file_kind_dir("docs", root))


def _wrapper(root, stem, body="line one\nline two"):
    """Write a legacy `.moytext` the way the old store did."""
    d = _docs_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / (stem + ".moytext")).write_text(
        json.dumps({"format": "moytext-v1", "body": body}))
    return d / (stem + ".moytext")


def test_a_document_is_the_file_and_nothing_wraps_it(tmp_path):
    root = _root(tmp_path)
    name = moy_carts.save_file("docs", "story", "# Title\n\nA line.", root)
    p = _docs_dir(root) / (name + ".md")
    assert p.read_text() == "# Title\n\nA line."
    assert moy_carts.load_file("docs", name, root) == "# Title\n\nA line."
    assert moy_carts.list_files("docs", root) == [name]


def test_a_moytext_wrapper_is_not_a_document(tmp_path):
    """The reader is STRICT: `.moytext` was the JSON wrapper a document used to
    come in, and nothing reads one now. A card carrying one shows it as ABSENT
    -- not listed, not loadable -- which is the whole of the no-migration policy
    (CLAUDE.md, 2026-09-07): the file is left alone for a person to remove."""
    root = _root(tmp_path)
    _wrapper(root, "from_a_card")
    assert moy_carts.list_files("docs", root) == []
    assert moy_carts.load_file("docs", "from_a_card", root) is None
    assert (_docs_dir(root) / "from_a_card.moytext").exists()


def test_decode_text_is_a_split_and_not_an_unwrapper(tmp_path):
    """A document IS its body, so the codec never looks inside it -- a note that
    happens to start with a brace is that text, not a header to unpack."""
    assert moy_carts.decode_text(WRAPPED) == [WRAPPED]
    assert moy_carts.decode_text("one\ntwo") == ["one", "two"]
    assert moy_carts.decode_text("") == [] and moy_carts.decode_text(None) == []


def test_sweep_store_prunes_retired_seeds_and_nothing_else(tmp_path):
    """The store-opening door, and what is behind it: ONE pass. A format change
    is not a sweep -- readers are strict and a seed version bump re-seeds the
    content -- so this door stays sized for `prune_retired`'s shape: gated on a
    generation sidecar, one small read on the warm path."""
    root = _root(tmp_path)
    moy_carts.ensure_dirs(root)
    gone = _root(tmp_path) + "/" + moy_carts.slug(moy_carts.RETIRED[0]) + ".moy"
    os.makedirs(gone)
    assert moy_carts.sweep_store(root) == 1
    assert not os.path.exists(gone)
    assert moy_carts.load_retired_version(root) == moy_carts.RETIRED_GEN
    assert moy_carts.sweep_store(root) == 0


def test_sprite_export_lands_in_files_sprites(tmp_path):
    from runtime.editors_sheet import SpriteSheet
    root = _root(tmp_path)
    sheet = SpriteSheet()
    sheet.pset(0, 0, 9)
    hexs = sheet.to_hex()
    name = moy_carts.save_file("sprites", moy_carts.new_file_name("sprites", root),
                               hexs, root)
    assert name in moy_carts.list_files("sprites", root)
    assert moy_carts.load_file("sprites", name, root) == hexs
    # A re-hydrated sheet matches the exported one (the reuse contract).
    assert SpriteSheet.from_hex(
        moy_carts.load_file("sprites", name, root)).to_hex() == hexs
# -- op-history sidecars (#111): files/.history/<kind>/<name>.jsonl ---------------

def test_history_sidecar_create_append_and_load(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("drawings", "castle", "PIXELS", root)
    assert moy_carts.load_history("drawings", "castle", root) == []   # none yet
    moy_carts.history_write_keyframe("drawings", "castle", {"w": 2}, root)
    moy_carts.history_append_segment("drawings", "castle", [["s", 0, 0, 5]], root)
    moy_carts.history_append_segment("drawings", "castle", [], root)   # empty -> no-op
    recs = moy_carts.load_history("drawings", "castle", root)
    assert [r["t"] for r in recs] == ["kf", "seg"]
    assert recs[0]["doc"] == {"w": 2}
    assert recs[1]["ops"] == [["s", 0, 0, 5]]
    # The sidecar lives at files/.history/drawings/castle.jsonl.
    assert moy_carts.history_path("drawings", "castle", root) == str(
        tmp_path / "files" / ".history" / "drawings" / "castle.jsonl")


def test_history_commit_writes_keyframe_then_segment(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("docs", "story", "TEXT", root)
    moy_carts.history_commit("docs", "story", [["ins", 0, "hi"]],
                             keyframe={"body": ""}, root=root)
    recs = moy_carts.load_history("docs", "story", root)
    assert [r["t"] for r in recs] == ["kf", "seg"]
    # A pure no-op commit never touches the sidecar.
    moy_carts.history_commit("docs", "story", [], keyframe=None, root=root)
    assert len(moy_carts.load_history("docs", "story", root)) == 2


def test_history_prune_keeps_last_keyframe_plus_n_segments(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("drawings", "art", "X", root)
    moy_carts.history_write_keyframe("drawings", "art", {"v": 1}, root)
    for i in range(5):
        moy_carts.history_append_segment("drawings", "art", [["s", i]], root)
    dropped = moy_carts.prune_history("drawings", "art", root, keep=2)
    assert dropped == 3                              # 1 kf + 5 seg -> 1 kf + 2 seg
    recs = moy_carts.load_history("drawings", "art", root)
    assert [r["t"] for r in recs] == ["kf", "seg", "seg"]
    assert [r["ops"] for r in recs[1:]] == [[["s", 3]], [["s", 4]]]  # the newest two


def test_ops_since_keyframe_is_the_one_sidecar_window():
    """The ONE reader every undo-seeding surface goes through (the editor
    handle, the Files role's history_ops): everything after the LAST keyframe,
    in order."""
    kf = {"t": "kf", "doc": "X"}
    seg = lambda *ops: {"t": "seg", "ops": list(ops)}
    assert moy_carts.ops_since_keyframe([]) == []
    assert moy_carts.ops_since_keyframe(None) == []
    assert moy_carts.ops_since_keyframe([seg(1), seg(2, 3)]) == [1, 2, 3]   # no kf yet
    assert moy_carts.ops_since_keyframe([seg(1), kf, seg(2), seg(3)]) == [2, 3]
    assert moy_carts.ops_since_keyframe([seg(1), kf, seg(2), kf]) == []
    assert moy_carts.ops_since_keyframe([seg(1), {"t": "seg"}]) == [1]      # ops-less seg


def test_history_load_drops_a_torn_last_line(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("drawings", "art", "X", root)
    moy_carts.history_write_keyframe("drawings", "art", {"v": 1}, root)
    with open(moy_carts.history_path("drawings", "art", root), "a") as f:
        f.write('{"t":"seg","ops":[[1,2  ')            # a torn append (power loss)
    recs = moy_carts.load_history("drawings", "art", root)
    assert [r["t"] for r in recs] == ["kf"]           # good record survives, torn dropped


def test_history_sidecar_follows_rename(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("drawings", "drawing_1", "a", root)
    moy_carts.history_append_segment("drawings", "drawing_1", [["s", 1]], root)
    assert moy_carts.rename_file("drawings", "drawing_1", "Castle", root) == "castle"
    assert moy_carts.load_history("drawings", "drawing_1", root) == []      # moved away
    assert moy_carts.load_history("drawings", "castle", root)[0]["ops"] == [["s", 1]]


def test_history_sidecar_copies_on_duplicate(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("drawings", "cat", "MEOW", root)
    moy_carts.history_append_segment("drawings", "cat", [["s", 7]], root)
    assert moy_carts.duplicate_file("drawings", "cat", root) == "cat_2"
    # Both the source and the copy carry the history (a copy is a real copy).
    assert moy_carts.load_history("drawings", "cat", root)[0]["ops"] == [["s", 7]]
    assert moy_carts.load_history("drawings", "cat_2", root)[0]["ops"] == [["s", 7]]


def test_history_sidecar_rides_trash_and_restore(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("drawings", "cat", "MEOW", root)
    moy_carts.history_append_segment("drawings", "cat", [["s", 3]], root)
    moy_carts.delete_file("drawings", "cat", root)
    assert moy_carts.load_history("drawings", "cat", root) == []            # gone from live
    moy_carts.restore_file("drawings", "cat", root)
    assert moy_carts.load_history("drawings", "cat", root)[0]["ops"] == [["s", 3]]


def test_history_sidecar_dropped_when_trash_is_emptied(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("drawings", "cat", "MEOW", root)
    moy_carts.history_append_segment("drawings", "cat", [["s", 3]], root)
    moy_carts.delete_file("drawings", "cat", root)
    moy_carts.empty_trash(root)
    # The trashed sidecar is gone with the trashed file.
    assert not os.path.exists(
        moy_carts._history_trash_path("drawings", "cat", root))


def test_history_dir_is_hidden_from_listing_and_is_not_a_kind(tmp_path):
    import pytest
    root = _root(tmp_path)
    moy_carts.save_file("drawings", "art", "X", root)
    moy_carts.history_append_segment("drawings", "art", [["s", 1]], root)
    # The .history sibling never appears as a kind item or in the trash listing,
    # and is not itself a valid kind (list/save against it are loud errors).
    assert moy_carts.list_files("drawings", root) == ["art"]
    assert (".history", "art") not in moy_carts.trash_list(root)
    for k, _n in moy_carts.trash_list(root):
        assert k in moy_carts.FILE_KINDS
    with pytest.raises(ValueError):
        moy_carts.list_files(".history", root)


def test_a_failed_prune_does_not_fail_the_commit(tmp_path, monkeypatch):
    """The append IS the commit. Failing it for a housekeeping error made the
    app skip mark_keyframe() for a keyframe already on disk, so every later
    flush wrote another one."""
    root = _root(tmp_path)
    moy_carts.save_file("docs", "story", "TEXT", root)
    before = moy_carts.history_prune_fails()

    def boom(*a, **k):
        raise OSError(28, "no space")

    monkeypatch.setattr(moy_carts, "prune_history", boom)
    err = moy_carts.history_commit("docs", "story", [["ins", 0, "hi"]],
                                   keyframe={"body": ""}, root=root)

    # The records landed, the failure is reported rather than raised, and it
    # is counted -- a swallowed error nothing can read is not an improvement.
    assert [r["t"] for r in moy_carts.load_history("docs", "story", root)] \
        == ["kf", "seg"]
    assert err is not None and "no space" in err
    assert moy_carts.history_prune_fails() == before + 1


def test_an_unpruned_sidecar_still_reads_the_right_window(tmp_path):
    """What makes the prune skippable: ops_since_keyframe reads only after the
    last keyframe, so records the prune failed to drop change nothing."""
    root = _root(tmp_path)
    moy_carts.save_file("docs", "story", "TEXT", root)
    recs = [{"t": "seg", "ops": [["old", 1]]},
            {"t": "kf", "doc": {"body": "base"}},
            {"t": "seg", "ops": [["new", 2]]}]
    path = moy_carts.history_path("docs", "story", root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    assert moy_carts.ops_since_keyframe(recs) == [["new", 2]]
    on_disk = moy_carts.load_history("docs", "story", root)
    assert moy_carts.ops_since_keyframe(on_disk) == [["new", 2]]


# ---------------------------------------------------------------------------
# the VAULT holds more than notes (docs/text_editing_2026-09.md)
# ---------------------------------------------------------------------------

def test_the_vault_lists_every_file_it_holds_under_its_whole_name(tmp_path):
    """`.md` is the one extension a vault name may leave off -- it is what a
    bare name MEANS. Everything else keeps it, because the extension is what
    picks the editing mode and what stops two files shadowing each other."""
    root = _root(tmp_path)
    for name in ("story", "todo.txt", "data.json", "hi.py", "hi.lua"):
        moy_carts.save_file("docs", name, "x", root)
    assert set(moy_carts.list_files("docs", root)) == {
        "story", "todo.txt", "data.json", "hi.py", "hi.lua"}
    assert moy_carts.count_files("docs", root) == 5
    # ...and each is on the card under exactly that name.
    assert moy_carts.file_path("docs", "story", root).endswith("story.md")
    for name in ("todo.txt", "data.json", "hi.py"):
        assert moy_carts.file_path("docs", name, root).endswith("/" + name)


def test_a_typed_title_keeps_its_extension_through_the_slug(tmp_path):
    root = _root(tmp_path)
    assert moy_carts.save_file("docs", "My Notes!.txt", "x", root) == \
        "my_notes.txt"
    assert moy_carts.load_file("docs", "my_notes.txt", root) == "x"


def test_a_free_name_is_the_title_when_it_is_free_and_numbered_when_not(tmp_path):
    root = _root(tmp_path)
    assert moy_carts.free_file_name("docs", "todo.txt", root) == "todo.txt"
    moy_carts.save_file("docs", "todo.txt", "x", root)
    # The counter goes BEFORE the extension: `todo.txt_2` would be stored as
    # `todo.txt_2.md` and stop being a text file at all.
    assert moy_carts.free_file_name("docs", "todo.txt", root) == "todo_2.txt"
    assert moy_carts.free_file_name("docs", "story", root) == "story"
    # A title with nothing readable in it auto-names rather than becoming
    # `slug`'s "cart" fallback.
    assert moy_carts.free_file_name("docs", "  ", root) == "doc_1"


def test_rename_and_duplicate_keep_a_vault_extension(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("docs", "todo.txt", "x", root)
    assert moy_carts.rename_file("docs", "todo.txt", "shopping.txt", root) == \
        "shopping.txt"
    assert moy_carts.load_file("docs", "shopping.txt", root) == "x"
    assert moy_carts.duplicate_file("docs", "shopping.txt", root) == \
        "shopping_2.txt"


def test_a_note_and_a_file_named_after_it_do_not_shadow_each_other(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("docs", "todo", "the note", root)
    moy_carts.save_file("docs", "todo.txt", "the text file", root)
    assert moy_carts.load_file("docs", "todo", root) == "the note"
    assert moy_carts.load_file("docs", "todo.txt", root) == "the text file"
    assert set(moy_carts.list_files("docs", root)) == {"todo", "todo.txt"}


def test_a_vault_file_goes_to_the_trash_and_comes_back_whole(tmp_path):
    root = _root(tmp_path)
    moy_carts.save_file("docs", "data.json", "{}", root)
    assert moy_carts.delete_file("docs", "data.json", root) == "data.json"
    assert moy_carts.list_files("docs", root) == []
    assert ("docs", "data.json") in [
        (k, n) for k, n, *_ in moy_carts.trash_list(root)]
    moy_carts.restore_file("docs", "data.json", root)
    assert moy_carts.load_file("docs", "data.json", root) == "{}"

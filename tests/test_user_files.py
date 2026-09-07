"""The #108 user-files layer: files/<kind>/ beside the carts dir -- the kind
registry, list/load/save/rename/duplicate verbs, the restorable trash, and the
one-shot artwork.moyimg migration. Same shared runtime/moy_carts.py the device
freezes."""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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


def test_artwork_migration_is_one_shot(tmp_path):
    root = _root(tmp_path)
    moy_carts.ensure_dirs(root)
    moy_carts.save_artwork("LEGACY-DRAWING", root)
    assert moy_carts.migrate_user_files(root) == "my_art"
    assert moy_carts.load_file("drawings", "my_art", root) == "LEGACY-DRAWING"
    # The legacy file stays (older builds keep booting against it) ...
    assert moy_carts.load_artwork(root) == "LEGACY-DRAWING"
    # ... and the migration never re-runs, even after the kind is emptied.
    moy_carts.delete_file("drawings", "my_art", root)
    moy_carts.empty_trash(root)
    assert moy_carts.migrate_user_files(root) is None
    assert moy_carts.list_files("drawings", root) == []


def test_migration_without_legacy_artwork_is_a_noop(tmp_path):
    root = _root(tmp_path)
    assert moy_carts.migrate_user_files(root) is None
    assert moy_carts.list_files("drawings", root) == []


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


# -- migrate docs ----------------------------------------------------------------

def test_migrate_docs_is_one_shot(tmp_path):
    import json
    root = _root(tmp_path)
    moy_carts.ensure_dirs(root)
    moy_carts.save_notes(json.dumps({"notes": [{"body": "hello"}]}), root)
    assert moy_carts.migrate_docs(root)
    assert len(moy_carts.list_files("docs", root)) == 1
    # Gated on the kind dir existing -> never re-runs.
    assert moy_carts.migrate_docs(root) is None


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


def test_migrate_doc_format_rewrites_wrappers_as_markdown(tmp_path):
    root = _root(tmp_path)
    moy_carts.ensure_dirs(root)
    _wrapper(root, "one")
    _wrapper(root, "two", "solo")
    moy_carts.save_file("docs", "native", "already md", root)
    assert moy_carts.migrate_doc_format(root) == 2
    d = _docs_dir(root)
    assert not list(d.glob("*.moytext"))
    assert (d / "one.md").read_text() == "line one\nline two"
    assert (d / "two.md").read_text() == "solo"
    assert (d / "native.md").read_text() == "already md"
    assert sorted(moy_carts.list_files("docs", root)) == ["native", "one", "two"]


def test_migrate_doc_format_is_one_shot_and_idempotent(tmp_path):
    root = _root(tmp_path)
    moy_carts.ensure_dirs(root)
    _wrapper(root, "one")
    assert moy_carts.migrate_doc_format(root) == 1
    assert moy_carts.load_docs_version(root) == moy_carts.DOCS_GEN
    # A store already at this generation costs one read and rewrites nothing.
    _wrapper(root, "late")
    assert moy_carts.migrate_doc_format(root) == 0
    assert (_docs_dir(root) / "late.moytext").exists()
    # A fresh store with nothing to move still marks itself swept.
    fresh = str(tmp_path / "fresh")
    moy_carts.ensure_dirs(fresh)
    assert moy_carts.migrate_doc_format(fresh) == 0
    assert moy_carts.load_docs_version(fresh) == moy_carts.DOCS_GEN


def test_an_existing_md_wins_over_an_older_wrapper(tmp_path):
    """The `.md` is either a NEWER note or the finished half of an interrupted
    pass, and neither may be overwritten by the wrapper beside it."""
    root = _root(tmp_path)
    moy_carts.ensure_dirs(root)
    moy_carts.save_file("docs", "note", "the newer text", root)
    _wrapper(root, "note", "the older text")
    assert moy_carts.migrate_doc_format(root) == 1
    d = _docs_dir(root)
    assert (d / "note.md").read_text() == "the newer text"
    assert not (d / "note.moytext").exists()


def test_a_crash_between_the_two_writes_loses_nothing(tmp_path):
    """`_write_atomic` publishes the `.md` whole BEFORE the wrapper is removed,
    so a power cut between them leaves both -- and the next pass finishes."""
    root = _root(tmp_path)
    moy_carts.ensure_dirs(root)
    legacy = _wrapper(root, "half", "recovered")
    d = _docs_dir(root)
    real_remove = moy_carts._remove

    def _die(path):
        if path.endswith(".moytext"):
            raise KeyboardInterrupt("power cut")
        real_remove(path)

    moy_carts._remove = _die
    try:
        with pytest.raises(KeyboardInterrupt):
            moy_carts.migrate_doc_format(root)
    finally:
        moy_carts._remove = real_remove
    assert (d / "half.md").read_text() == "recovered"    # step one completed
    assert legacy.exists()                               # step two did not
    assert moy_carts.load_docs_version(root) == 0        # so the pass re-runs
    assert moy_carts.migrate_doc_format(root) == 1
    assert (d / "half.md").read_text() == "recovered"
    assert not legacy.exists()


def test_a_stray_wrapper_after_the_sweep_is_absorbed_on_listing(tmp_path):
    """A `.moytext` pushed by an older peer, or carried in on a card, arrives
    after the one-shot pass -- so the docs listing absorbs it in place."""
    root = _root(tmp_path)
    moy_carts.ensure_dirs(root)
    assert moy_carts.migrate_doc_format(root) == 0
    _wrapper(root, "from_a_card", "hello there")
    assert moy_carts.list_files("docs", root) == ["from_a_card"]
    assert moy_carts.load_file("docs", "from_a_card", root) == "hello there"
    assert not (_docs_dir(root) / "from_a_card.moytext").exists()


def test_a_trashed_wrapper_migrates_with_the_live_ones(tmp_path):
    root = _root(tmp_path)
    moy_carts.ensure_dirs(root)
    moy_carts.save_file("docs", "gone", "x", root)
    moy_carts.delete_file("docs", "gone", root)
    trash = Path(moy_carts.files_root(root)) / "trash" / "docs"
    (trash / "gone.md").rename(trash / "gone.moytext")
    (trash / "gone.moytext").write_text(WRAPPED)
    assert moy_carts.migrate_doc_format(root) == 1
    assert ("docs", "gone") in moy_carts.trash_list(root)
    assert (trash / "gone.md").read_text() == "line one\nline two"


def test_sweep_store_runs_its_one_shot_passes(tmp_path):
    """The store-opening door: retire, migrate the legacy notebook, rewrite the
    doc format. `migrate_docs` runs HERE since the notebook app was deleted --
    it builds the vault a note is picked from, so nothing can list it first."""
    root = _root(tmp_path)
    moy_carts.ensure_dirs(root)
    _wrapper(root, "one")
    assert moy_carts.sweep_store(root) == (0, None, 1)
    assert moy_carts.load_retired_version(root) == moy_carts.RETIRED_GEN
    assert moy_carts.load_docs_version(root) == moy_carts.DOCS_GEN
    assert moy_carts.sweep_store(root) == (0, None, 0)


def test_sweep_store_migrates_a_legacy_notebook(tmp_path):
    """The pass the notebook app used to trigger on its own open. Its own
    marker is the docs dir, so a store that already has one is left alone."""
    root = _root(tmp_path)
    moy_carts.ensure_dirs(root)
    moy_carts.save_notes('{"notes": [{"body": "my first note"}]}', root)
    _retired, made, _fmt = moy_carts.sweep_store(root)
    assert made and moy_carts.load_file("docs", made[0], root) == "my first note"


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

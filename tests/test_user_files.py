"""The #108 user-files layer: files/<kind>/ beside the carts dir -- the kind
registry, list/load/save/rename/duplicate verbs, the restorable trash, and the
one-shot artwork.moyimg migration. Same shared runtime/moy_carts.py the device
freezes."""

import json
import subprocess
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import moy_carts  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_seed_folders():
    """`_SEED_FOLDERS` is process state a seeding door writes, so a test that
    builds a workstation would otherwise leave the picture pass stepping over
    folders the next test made itself."""
    moy_carts.note_seed_folders(())
    yield
    moy_carts.note_seed_folders(())


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
    it builds the vault a note is picked from, so nothing can list it first.

    Three passes, not four: the picture pass left this door on 2026-09-07 and
    the test below says why."""
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


# -- pictures became one format (2026-09-07) ---------------------------------
#
# `migrate_images` is the third one-shot pass behind `sweep_store`, and the only
# one that rewrites a KID'S OWN files rather than the shell's -- so it is checked
# for pixel identity, for crash safety, and for being a no-op the second time.
# The retired codec's WRITER is built here on purpose: nothing in the tree writes
# one any more, which is the point of the change.

def _pic_store(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    return root


def _pic(w, h):
    """A picture with every palette index, a long run and a noisy tail."""
    raw = bytearray(bytes((7,)) * min(700, w * h))
    raw.extend(bytes(range(64)))
    raw.extend(bytes(((i * 37) & 63) for i in range(w * h)))
    return bytes(raw[:w * h])


def _rle_blob(w, h, indices, **extra):
    """What Paint wrote before 2026-09-07 -- built here because nothing in the
    tree writes it any more, which is the point."""
    packed = bytearray()
    pos = 0
    while pos < len(indices):
        value = indices[pos] & 63
        count = 1
        while (pos + count < len(indices) and count < 255
               and (indices[pos + count] & 63) == value):
            count += 1
        packed.append(count)
        packed.append(value)
        pos += count
    meta = {"format": "moyimg-v1", "w": w, "h": h, "codec": "rle",
            "data": moy_carts._b64_encode(packed)}
    meta.update(extra)
    return json.dumps(meta)


def _restored(root):
    """The binned drawing, read back the way a kid gets it: out of the trash."""
    moy_carts.restore_file("drawings", "binned", root)
    return moy_carts.load_file("drawings", "binned", root)


def test_the_sweep_rewrites_a_kids_drawing_pixel_for_pixel(tmp_path):
    root = _pic_store(tmp_path)
    art = _pic(64, 48)
    moy_carts.save_file("drawings", "sunset", _rle_blob(64, 48, art), root)
    assert moy_carts.migrate_images(root) == 1
    blob = moy_carts.load_file("drawings", "sunset", root)
    assert "codec" not in json.loads(blob)
    assert moy_carts.decode_moyimg(blob) == (64, 48, art)


def test_the_sweep_reaches_carts_the_trash_and_the_wallpaper_copy(tmp_path):
    """Everywhere a picture can be. The trash especially: it is restorable, so
    a wrapper left there hands back a picture nothing can read."""
    root = _pic_store(tmp_path)
    art = _pic(16, 16)
    cart = moy_carts.create("Covered", root, src="def _draw():\n    pass\n")
    moy_carts.save_image(cart, "cover", _rle_blob(16, 16, art))
    moy_carts.save_artwork(_rle_blob(16, 16, art), root)
    moy_carts.save_file("drawings", "kept", _rle_blob(16, 16, art), root)
    moy_carts.save_file("drawings", "binned", _rle_blob(16, 16, art), root)
    moy_carts.delete_file("drawings", "binned", root)

    assert moy_carts.migrate_images(root) == 4
    for blob in (moy_carts.load_image(cart["path"], "cover"),
                 moy_carts.load_artwork(root),
                 moy_carts.load_file("drawings", "kept", root),
                 _restored(root)):
        assert moy_carts.decode_moyimg(blob) == (16, 16, art)


def test_the_sweep_keeps_the_provenance_stamp(tmp_path):
    """`src`/`sig` is what powers "your drawing changed -> UPDATE". Rewriting
    the picture through the plain encoder would drop it, and the affordance
    would simply stop appearing with nothing to point at."""
    root = _pic_store(tmp_path)
    art = _pic(8, 8)
    cart = moy_carts.create("Story", root, src="def _draw():\n    pass\n")
    moy_carts.save_image(cart, "bg", _rle_blob(8, 8, art, src="drawings/sun",
                                               sig=4242))
    moy_carts.migrate_images(root)
    blob = moy_carts.load_image(cart["path"], "bg")
    assert moy_carts.read_provenance(blob) == ("drawings/sun", 4242)


def test_a_picture_already_in_the_one_format_is_not_touched(tmp_path):
    root = _pic_store(tmp_path)
    art = _pic(16, 16)
    blob = moy_carts.encode_moyimg(16, 16, art)
    moy_carts.save_file("drawings", "fine", blob, root)
    assert moy_carts.migrate_images(root) == 0
    assert moy_carts.load_file("drawings", "fine", root) == blob


def test_the_sweep_runs_once_and_the_warm_path_is_a_read(tmp_path):
    root = _pic_store(tmp_path)
    art = _pic(16, 16)
    moy_carts.save_file("drawings", "one", _rle_blob(16, 16, art), root)
    assert moy_carts.migrate_images(root) == 1
    assert moy_carts.load_images_version(root) == moy_carts.IMAGES_GEN
    # A wrapper that arrives AFTER the pass (pushed by an older peer over the
    # sync RPC) is not swept -- the gate is the whole point of the gate.
    moy_carts.save_file("drawings", "two", _rle_blob(16, 16, art), root)
    assert moy_carts.migrate_images(root) == 0


def test_a_crash_between_two_files_leaves_the_finished_ones_finished(tmp_path):
    """The marker is written LAST, so an interrupted pass has converted some
    pictures and recorded nothing -- and the next boot finishes the rest and
    steps over the ones already done."""
    root = _pic_store(tmp_path)
    art = _pic(16, 16)
    for name in ("a", "b", "c"):
        moy_carts.save_file("drawings", name, _rle_blob(16, 16, art), root)

    real = moy_carts._rewrite_image
    calls = []

    def die_after_two(path):
        if len(calls) >= 2:
            raise KeyboardInterrupt("power cut")
        calls.append(path)
        return real(path)

    moy_carts._rewrite_image = die_after_two
    try:
        with pytest.raises(KeyboardInterrupt):
            moy_carts.migrate_images(root)
    finally:
        moy_carts._rewrite_image = real

    assert moy_carts.load_images_version(root) == 0, "the marker must not be there"
    assert moy_carts.migrate_images(root) >= 1
    for name in ("a", "b", "c"):
        blob = moy_carts.load_file("drawings", name, root)
        assert moy_carts.decode_moyimg(blob) == (16, 16, art)
        assert "codec" not in json.loads(blob)


def test_a_blob_that_will_not_parse_is_left_exactly_as_it_is(tmp_path):
    """It was unreadable before the pass and inventing a replacement would be
    worse than leaving it for a person to find."""
    root = _pic_store(tmp_path)
    junk = '{"format": "moyimg-v1", "w": 8, "h": 8, "codec": "rle", "data": "@@"}'
    moy_carts.save_file("drawings", "broken", junk, root)
    assert moy_carts.migrate_images(root) == 0
    assert moy_carts.load_file("drawings", "broken", root) == junk


def test_the_store_door_does_not_run_the_picture_pass(tmp_path):
    """It did for one day, and that day it cost a Guition 196 seconds of boot
    and then the boot. `sweep_store` is a store OPENING -- a small read warm,
    bounded work cold -- and rewriting every picture on a card is neither."""
    root = _pic_store(tmp_path)
    blob = _rle_blob(8, 8, _pic(8, 8))
    moy_carts.save_file("drawings", "one", blob, root)
    assert len(moy_carts.sweep_store(root)) == 3
    assert moy_carts.load_file("drawings", "one", root) == blob
    assert moy_carts.load_images_version(root) == 0


def test_the_job_rewrites_one_picture_per_step(tmp_path):
    """The console drives this from the idle branch of its frame loop, so a
    step is a whole FILE and no more: on an S3 one 320x240 picture is seconds
    of compressor, and a step that took two would double a freeze nobody asked
    for."""
    root = _pic_store(tmp_path)
    art = _pic(16, 16)
    for name in ("a", "b", "c"):
        moy_carts.save_file("drawings", name, _rle_blob(16, 16, art), root)
    job = moy_carts.ImageMigration(root)
    assert job.step() and job.rewritten == 1
    assert job.step() and job.rewritten == 2
    assert job.step() and job.rewritten == 3
    assert job.step() is False               # exhausted: the stamp lands here
    assert job.done and moy_carts.load_images_version(root) == moy_carts.IMAGES_GEN
    for name in ("a", "b", "c"):
        assert moy_carts.decode_moyimg(
            moy_carts.load_file("drawings", name, root)) == (16, 16, art)


def test_a_store_already_at_this_generation_is_never_walked(tmp_path):
    """The warm path is the one small read `load_images_version` costs -- the
    job must not listdir a card it has nothing to do on."""
    root = _pic_store(tmp_path)
    moy_carts.save_file("drawings", "one", _rle_blob(8, 8, _pic(8, 8)), root)
    moy_carts.migrate_images(root)
    listed = []
    real = moy_carts.os.listdir
    moy_carts.os.listdir = lambda d, _r=real: (listed.append(d), _r(d))[1]
    try:
        job = moy_carts.ImageMigration(root)
        assert job.done and job.step() is False
    finally:
        moy_carts.os.listdir = real
    assert listed == []


def test_a_seed_carts_pictures_are_stepped_over(tmp_path):
    """The seed pass owns those folders and replaces them wholesale on the next
    version bump, so paying 15s a cover to rewrite what a re-seed overwrites is
    work with a negative return. A kid's drawing has no such second author,
    which is why it is the one thing this pass exists for."""
    root = _pic_store(tmp_path)
    art = _pic(16, 16)
    seeded = moy_carts.create("Hop Quest", root, src="def _draw():\n    pass\n")
    mine = moy_carts.create("My Game", root, src="def _draw():\n    pass\n")
    moy_carts.save_image(seeded, "cover", _rle_blob(16, 16, art))
    moy_carts.save_image(mine, "cover", _rle_blob(16, 16, art))
    moy_carts.save_file("drawings", "sunset", _rle_blob(16, 16, art), root)

    skip = moy_carts.seed_folders([{"title": "Hop Quest", "type": "game"}])
    assert skip == ("hop_quest.moy",)
    assert moy_carts.migrate_images(root, skip=skip) == 2
    assert "codec" in json.loads(moy_carts.load_image(seeded["path"], "cover"))
    assert "codec" not in json.loads(moy_carts.load_image(mine["path"], "cover"))
    assert "codec" not in json.loads(
        moy_carts.load_file("drawings", "sunset", root))


def test_a_seed_folder_set_is_read_off_either_roster_form(tmp_path):
    """A board freezes the PACKED roster and the host carries dicts; the pass
    steps over the same folders either way."""
    packed = [("Hop Quest", 3, b""), ("Sky Run", 1, b"")]
    plain = [{"title": "Hop Quest"}, {"title": "Sky Run"}]
    assert (moy_carts.seed_folders(packed) == moy_carts.seed_folders(plain)
            == ("hop_quest.moy", "sky_run.moy"))
    assert moy_carts.seed_folders([]) == ()


def test_the_kids_own_drawings_are_rewritten_first(tmp_path):
    """The order is the priority. A drawing is the only picture here nothing
    else can replace, and the pass may be interrupted at any step."""
    root = _pic_store(tmp_path)
    art = _pic(8, 8)
    cart = moy_carts.create("Mine", root, src="def _draw():\n    pass\n")
    moy_carts.save_image(cart, "cover", _rle_blob(8, 8, art))
    moy_carts.save_file("drawings", "sunset", _rle_blob(8, 8, art), root)
    moy_carts.save_artwork(_rle_blob(8, 8, art), root)

    seen = []
    real = moy_carts._rewrite_image
    moy_carts._rewrite_image = lambda p, _r=real: (seen.append(p), _r(p))[1]
    try:
        moy_carts.migrate_images(root, skip=())
    finally:
        moy_carts._rewrite_image = real
    assert seen[0].endswith(moy_carts.ARTWORK_NAME)
    assert "/drawings/" in seen[1]
    assert seen[-1].endswith("/images/cover.moyimg")


def test_a_picture_the_heap_refuses_is_skipped_and_holds_the_stamp(tmp_path):
    """The failure that is not the file's fault. It is skipped, counted, said
    once, and the generation stamp is NOT written -- so the next session sweeps
    again and finishes the job, instead of recording a store as converged while
    a kid's drawing is still in a format nothing can read."""
    root = _pic_store(tmp_path)
    art = _pic(16, 16)
    for name in ("a", "b"):
        moy_carts.save_file("drawings", name, _rle_blob(16, 16, art), root)

    real = moy_carts._deflate_pieces
    calls = []

    def starved(pieces, _r=real):
        calls.append(1)
        if len(calls) == 1:
            raise MemoryError("memory allocation failed")
        return _r(pieces)

    moy_carts._deflate_pieces = starved
    try:
        job = moy_carts.ImageMigration(root, skip=())
        while job.step():
            pass
    finally:
        moy_carts._deflate_pieces = real
    assert (job.rewritten, job.failed) == (1, 1)
    assert moy_carts.load_images_version(root) == 0, "a partial sweep must not stamp"

    assert moy_carts.migrate_images(root, skip=()) == 1     # the next pass finishes it
    assert moy_carts.load_images_version(root) == moy_carts.IMAGES_GEN
    for name in ("a", "b"):
        assert moy_carts.decode_moyimg(
            moy_carts.load_file("drawings", name, root)) == (16, 16, art)


def test_a_blob_that_will_not_parse_does_not_hold_the_stamp_forever(tmp_path):
    """The other half of that rule, and it is a different answer. A corrupt blob
    fails identically on every future pass, so blocking the stamp on it would
    re-walk the whole store every boot forever and still not fix it."""
    root = _pic_store(tmp_path)
    junk = '{"format": "moyimg-v1", "w": 8, "h": 8, "codec": "rle", "data": "@@"}'
    moy_carts.save_file("drawings", "broken", junk, root)
    moy_carts.save_file("drawings", "fine", _rle_blob(8, 8, _pic(8, 8)), root)
    assert moy_carts.migrate_images(root, skip=()) == 1
    assert moy_carts.load_file("drawings", "broken", root) == junk
    assert moy_carts.load_images_version(root) == moy_carts.IMAGES_GEN


def test_a_picture_is_rewritten_without_ever_holding_its_raster(tmp_path):
    """The reason there is a job at all. Materialising the 320x240 raster is
    what fragmented a Guition's heap past recovery -- six carts failed to load
    and the desktop died asking for 76,800 bytes -- so the retired runs are
    expanded INTO the compressor a piece at a time and the biggest thing this
    holds is a kilobyte."""
    root = _pic_store(tmp_path)
    art = _pic(320, 240)
    moy_carts.save_file("drawings", "big", _rle_blob(320, 240, art), root)

    biggest = []
    real = moy_carts._deflate_pieces

    def watched(pieces, _r=real):
        return _r(_watch(pieces, biggest))

    def _watch(pieces, out):
        for piece in pieces:
            out.append(len(piece))
            yield piece

    moy_carts._deflate_pieces = watched
    try:
        assert moy_carts.migrate_images(root, skip=()) == 1
    finally:
        moy_carts._deflate_pieces = real
    assert sum(biggest) == 320 * 240, "the whole picture must reach the compressor"
    assert len(biggest) > 60, "one piece is a whole raster by another name"
    assert max(biggest) <= moy_carts.IMAGES_PIECE + 255
    assert moy_carts.decode_moyimg(
        moy_carts.load_file("drawings", "big", root)) == (320, 240, art)


def test_a_picture_not_yet_rewritten_reads_as_absent(tmp_path):
    """What makes deferring the pass cost a thumbnail rather than a crash: a
    drawing still in the retired codec is a picture the shelf does not have
    YET, and every reader on every tier already answers that with a
    placeholder."""
    root = _pic_store(tmp_path)
    blob = _rle_blob(64, 48, _pic(64, 48))
    moy_carts.save_file("drawings", "waiting", blob, root)
    assert moy_carts.decode_moyimg(blob) is None
    assert moy_carts.moyimg_runs(blob) is None


def test_the_consoles_idle_frames_are_what_run_the_picture_pass(tmp_path):
    """A migration nothing calls is a migration that never happens, and the door
    it used to have (`sweep_store`, i.e. the boot) is the one door it must not
    use. This is the wiring: the desk is up, the redraw gate skipped a frame,
    and one picture is rewritten on it."""
    from runtime import host_app
    root = _pic_store(tmp_path)
    art = _pic(16, 16)
    moy_carts.save_file("drawings", "sunset", _rle_blob(16, 16, art), root)
    ws = host_app.build_workstation(root)
    assert moy_carts.load_images_version(root) == 0, "the boot must not have swept"

    ws._dirty = False
    ws._quiet_frames = 20
    for _ in range(60):
        ws.frame(0.05)
    assert moy_carts.decode_moyimg(
        moy_carts.load_file("drawings", "sunset", root)) == (16, 16, art)
    assert moy_carts.load_images_version(root) == moy_carts.IMAGES_GEN
    assert ws._pic_migration.rewritten == 1, "only the kid's drawing was pending"


# -- and the whole rewrite on the interpreter a board runs --------------------

_REWRITE_DRIVER = """
import sys
sys.path.insert(0, ".")
import gc, json, moy_carts, moy_gfx

art = bytes(((i // 320) // 5 + (i % 320) // 7) & 63 for i in range(320 * 240))
packed = bytearray()
pos = 0
while pos < len(art):
    value = art[pos]
    count = 1
    while pos + count < len(art) and count < 255 and art[pos + count] == value:
        count += 1
    packed.append(count)
    packed.append(value)
    pos += count

# The retired blob, written here because nothing writes one any more.
open("pic.moyimg", "w").write(json.dumps({
    "format": "moyimg-v1", "w": 320, "h": 240, "codec": "rle",
    "src": "drawings/sun", "sig": 4242,
    "data": moy_carts._b64_encode(packed)}))

gc.collect()
before = gc.mem_free()
assert moy_carts._rewrite_image("pic.moyimg") == 1, "the rewrite refused the picture"
low = gc.mem_free()
meta = json.loads(open("pic.moyimg").read())
assert "codec" not in meta and meta["src"] == "drawings/sun" and meta["sig"] == 4242
got = moy_carts.decode_moyimg(open("pic.moyimg").read())
assert got is not None and bytes(got[2]) == art, "the rewrite lost the picture"

# The streamed reader on the same file, and the pieces it hands the compressor.
biggest = 0
for piece in moy_carts._rle_pieces(bytes(packed), 320 * 240):
    if len(piece) > biggest:
        biggest = len(piece)

print("RESULT " + json.dumps({
    "held": before - low, "bytes": len(open("pic.moyimg").read()),
    "rle_bytes": len(packed) + 4, "piece": biggest,
    "runs": len(moy_carts.moyimg_runs(open("pic.moyimg").read())[2]),
}))
"""


def test_the_rewrite_runs_on_the_interpreter_the_board_runs(tmp_path):
    """The path that took a Guition down, on the tier it took down. Nothing on
    the host reaches it: CPython has no `deflate` to write through piecewise and
    no `moy_gfx` to expand the retired runs into a kilobyte at a time, so both
    halves of the streaming rewrite are CPython stand-ins up there."""
    import unix_mp
    exe = unix_mp.require_unix_mp("moy_gfx", why=(
        "The picture migration's two streaming halves -- the board's own\n"
        "compressor written a piece at a time, and the NATIVE run expander\n"
        "aimed at a reused kilobyte instead of a 76,800-byte block -- exist\n"
        "on no other tier."))
    for name in ("moy_carts.py", "moy_image.py", "moy_fs.py", "moy_journal.py"):
        (tmp_path / name).write_text(
            (ROOT / "runtime" / name).read_text(encoding="utf-8"),
            encoding="utf-8")
    (tmp_path / "run.py").write_text(_REWRITE_DRIVER)

    r = subprocess.run([exe, "run.py"], cwd=str(tmp_path), capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, "%s\n%s" % (r.stdout[-3000:], r.stderr[-3000:])
    line = [l for l in r.stdout.split("\n") if l.startswith("RESULT ")]
    assert line, r.stdout[-3000:]
    got = json.loads(line[0][len("RESULT "):])
    # The whole subject: no piece of raster anywhere near 76,800 bytes, and the
    # rewrite's peak hold nowhere near it either. `held` is generous (it counts
    # the file text and its base64 too, both of which the rewrite must hold at
    # least briefly) -- what it rules out is the raster on top of them.
    assert got["piece"] <= moy_carts.IMAGES_PIECE + 255
    assert got["held"] < 320 * 240, got
    assert got["runs"] > 0 and got["bytes"] < got["rle_bytes"]
    print("\nunix MicroPython: 320x240 rewrite held %d B peak, %d B -> %d B"
          % (got["held"], got["rle_bytes"], got["bytes"]))

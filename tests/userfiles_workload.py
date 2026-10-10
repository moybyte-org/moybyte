"""A generated workload over the user-files layer (native/moy_store/moy_ufiles.h)
and the digests that pin it: `run(api, root)` drives one seeded sequence of
store verbs and returns, per verb, its name and a digest of its answer and of
every byte under the store's parent folder afterwards.

`api` is a mapping of the layer's names (`moy_carts`' re-exports: save_file,
list_files, history_commit, ...) to callables taking the store root as their
last positional argument, as every one of them does. The digests in
tests/fixtures/userfiles_golden.json were made from the Python layer the C
replaced (runtime/moy_files.py and runtime/moy_file_ops.py, deleted in the
same change), so a store the C writes is byte for byte the one the Python
wrote, the publish marker, the backups, the sidecars and the journal included.

The clock is the workload's: after each verb every path whose mtime is a real
one is set to the verb's step, so "newest first" means the same order in every
run, and the journal's timestamp is fixed by the caller.
"""

import hashlib
import json
import os
import random

SEED = 2026_10_10
STEPS = 420
FAKE_T0 = 10_000            # a mtime below this is the workload's own
KINDS = ("drawings", "docs", "sprites", "music")
TITLES = ("Dragon", "dragon", "My Song", "todo.txt", "hello.py", "game.lua",
          "data.json", "story", "  ", "!!!", "a-b c", "x", "Notes 2",
          "café", "hello.py.txt", "UPPER CASE", "tab\there")
BLOBS = ("", "x", "line one\nline two\n", "{\"format\": \"moyimg-v1\", \"w\": 1, "
         "\"h\": 1, \"data\": \"eJxjAAAAAQAB\"}", "café ☃\n" * 3,
         "{\"a\": 1, \"src\": \"old/x\", \"b\": [1, 2]}", "[1, 2, 3]", "{}",
         "not json {", "{\"sig\": \"12\", \"src\": \"docs/n\"}")


def _norm(v):
    """An answer as JSON can write it: tuples as lists, bytes as hex."""
    if isinstance(v, (bytes, bytearray)):
        return {"bytes": bytes(v).hex()}
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in sorted(v.items())}
    return v


def _tree(top, step):
    """The digest of every folder's path and every file's path and bytes under
    `top` (the publish marker's absolute path read as `<top>`), after setting
    each real mtime to the workload's clock."""
    h = hashlib.sha256()
    for d, dirs, files in sorted(os.walk(top)):
        dirs.sort()
        for n in sorted(dirs + files):
            p = os.path.join(d, n)
            if os.stat(p).st_mtime >= FAKE_T0:
                os.utime(p, (step, step))
        h.update(os.path.relpath(d, top).encode() + b"/\2")
        for n in sorted(files):
            p = os.path.join(d, n)
            h.update(os.path.relpath(p, top).encode() + b"\0")
            with open(p, "rb") as f:
                h.update(f.read().replace(top.encode(), b"<top>"))
            h.update(b"\1")
    return h.hexdigest()[:16]


def _call(fn, *a, **kw):
    try:
        return ["ok", _norm(fn(*a, **kw))]
    except ValueError:
        return ["ValueError"]
    except OSError:
        return ["OSError"]


def run(api, root):
    """[(verb, digest)] for the seeded workload over the store at `root`."""
    rng = random.Random(SEED)
    top = os.path.dirname(root)
    os.makedirs(root, exist_ok=True)
    proj = "local.demo.moy"
    os.makedirs(os.path.join(root, proj, "scenes"), exist_ok=True)
    os.makedirs(os.path.join(root, proj, "images"), exist_ok=True)
    for n, body in (("manifest.json", "{\"title\": \"demo\"}"), ("main.py", "x = 1\n"),
                    ("scenes/a.moyscene", "{}"), ("images/b.moyimg", "{}"),
                    ("zz.txt", "z"), ("main.py.bak", "junk")):
        with open(os.path.join(root, proj, n), "w") as f:
            f.write(body)
    _tree(top, 1)
    pk = api["project_kind"](os.path.join(root, proj))
    known = {k: [] for k in KINDS}
    log = []
    for step in range(STEPS):
        kind = rng.choice(KINDS)
        names = known[kind]
        name = rng.choice(names) if names and rng.random() < 0.85 else rng.choice(TITLES)
        blob = rng.choice(BLOBS)
        r = rng.random()
        if r < 0.20:
            verb, ans = "save", _call(api["save_file"], kind, name, blob, root)
            if ans[0] == "ok" and ans[1] not in names:
                names.append(ans[1])
        elif r < 0.27:
            verb, ans = "load", _call(api["load_file"], kind, name, root)
        elif r < 0.33:
            verb, ans = "list", _call(api["list_files"], kind, root)
        elif r < 0.36:
            verb, ans = "count", _call(api["count_files"], kind, root)
        elif r < 0.40:
            verb, ans = "new_name", _call(api["new_file_name"], kind, root)
        elif r < 0.44:
            verb, ans = "free_name", _call(api["free_file_name"], kind,
                                           rng.choice(TITLES), root)
        elif r < 0.50:
            verb, ans = "rename", _call(api["rename_file"], kind, name,
                                        rng.choice(TITLES), root)
            if ans[0] == "ok" and name in names:
                names[names.index(name)] = ans[1]
        elif r < 0.55:
            verb, ans = "duplicate", _call(api["duplicate_file"], kind, name, root)
            if ans[0] == "ok":
                names.append(ans[1])
        elif r < 0.61:
            verb, ans = "delete", _call(api["delete_file"], kind, name, root)
            if ans[0] == "ok" and name in names:
                names.remove(name)
        elif r < 0.65:
            verb, ans = "trash_list", _call(api["trash_list"], root)
        elif r < 0.69:
            got = api["trash_list"](root)
            pick = [n for k, n in got if k == kind]
            verb, ans = "restore", (_call(api["restore_file"], kind, rng.choice(pick), root)
                                    if pick else ["none"])
            if ans[0] == "ok":
                names.append(ans[1])
        elif r < 0.70:
            verb, ans = "empty_trash", _call(api["empty_trash"], root)
        elif r < 0.71:
            verb, ans = "prune_trash", _call(api["prune_trash"], root, rng.choice((0, 2, 5)))
        elif r < 0.80:
            # One key an object: a MicroPython dict keeps no order, and the
            # sidecar is json.dumps' bytes on every tier.
            ops = [{"op": [rng.randrange(9), rng.choice(BLOBS)]}
                   for _ in range(rng.randrange(4))]
            kf = rng.choice((None, None, {"doc": [blob, step]}, [step, "kf"]))
            verb, ans = "history_commit", _call(api["history_commit"], kind, name,
                                                ops, kf, root)
        elif r < 0.84:
            verb, ans = "history", _call(api["load_history"], kind, name, root)
        elif r < 0.87:
            verb, ans = "history_ops", _call(api["history_ops"], kind, name, root)
        elif r < 0.88:
            verb, ans = "prune_history", _call(api["prune_history"], kind, name,
                                               root, rng.choice((0, 1, 3)))
        elif r < 0.89:
            verb, ans = "clear_history", _call(api["clear_history"], kind, name, root)
        elif r < 0.92:
            src = rng.choice(BLOBS)
            sig = api["content_sig"](src)
            stamped = api["stamp_provenance"](src, kind, name, sig)
            verb, ans = "stamp", ["ok", [sig, stamped, _norm(api["read_provenance"](stamped)),
                                         _norm(api["read_provenance"](src))]]
        elif r < 0.95:
            which = rng.choice(("manifest.json", "config.json", "scenes/a.moyscene",
                                "main.py", "new.txt"))
            verb, ans = "project_save", _call(api["save_file"], pk, which, blob, root)
        elif r < 0.97:
            verb, ans = "project_list", _call(api["list_files"], pk, root)
        elif r < 0.98:
            which = rng.choice(("manifest.json", "scenes/a.moyscene", "../x",
                                "a/b/c", "nope.txt"))
            verb, ans = "project_load", _call(api["load_file"], pk, which, root)
        else:
            verb, ans = "paths", ["ok", [api["file_path"](kind, name, root),
                                         api["files_root"](root),
                                         api["vault_ext"](name), api["script_ext"](name),
                                         api["project_folder"](pk)]]
        if verb == "paths":
            ans = ["ok", [os.path.relpath(p, top) if isinstance(p, str) and p.startswith(top)
                          else p for p in ans[1]]]
        _log(log, verb, ans, top, 100 + step)
    for verb, ans in _tail(api, root, rng, top):
        _log(log, verb, ans, top, 100 + STEPS + len(log))
    return log


def _log(log, verb, ans, top, step):
    digest = hashlib.sha256(json.dumps([verb, ans], sort_keys=True).encode())
    log.append((verb, digest.hexdigest()[:12] + "/" + _tree(top, step)))


def _tail(api, root, rng, top):
    """The verbs the random walk reaches rarely: a sidecar long enough to
    prune, the folder kind, and the codecs (by what decodes: the compressed
    bytes are each tier's own)."""
    for i in range(40):
        kf = {"doc": "v%d" % i} if i == 7 else None
        yield "commit", _call(api["history_commit"], "docs", "long", [{"i": i}], kf, root)
    yield "history", _call(api["load_history"], "docs", "long", root)
    yield "history_ops", _call(api["history_ops"], "docs", "long", root)
    yield "prune", _call(api["prune_history"], "docs", "long", root, 3)
    yield "history", _call(api["load_history"], "docs", "long", root)
    take = os.path.join(api["files_root"](root), "recordings", "take1")
    os.makedirs(os.path.join(take, "inner"), exist_ok=True)
    for n, body in (("a.wav", "A"), ("inner/b.wav", "B")):
        with open(os.path.join(take, n), "w") as f:
            f.write(body)
    yield "rec_list", _call(api["list_files"], "recordings", root)
    yield "rec_count", _call(api["count_files"], "recordings", root)
    yield "rec_load", _call(api["load_file"], "recordings", "take1", root)
    yield "rec_save", _call(api["save_file"], "recordings", "x", "y", root)
    yield "rec_dup", _call(api["duplicate_file"], "recordings", "take1", root)
    yield "rec_del", _call(api["delete_file"], "recordings", "take1", root)
    yield "trash", _call(api["trash_list"], root)
    yield "rec_restore", _call(api["restore_file"], "recordings", "take1", root)
    yield "empty", _call(api["empty_trash"], root)
    yield "trash", _call(api["trash_list"], root)
    yield "bad_kind", _call(api["list_files"], "selfies", root)
    yield "bad_project", _call(api["load_file"], "project:", "x", root)
    for w, h in ((1, 1), (7, 3), (64, 48)):
        pix = bytes(rng.randrange(64) if rng.random() < 0.3 else 5 for _ in range(w * h))
        blob = api["encode_image"](w, h, pix)
        got = api["decode_image"](blob)
        yield "image", ["ok", [w, h, got == (w, h, pix) or [got[0], got[1], bytes(got[2]) == pix]]]
    yield "image_bad", _call(api["encode_image"], 2, 2, b"abc")
    yield "image_not", ["ok", [api["decode_image"](b) for b in ("x", "{}", "{\"w\": 1}")]]
    pix = bytes(rng.randrange(64) for _ in range(128 * 128))
    got = api["decode_cover"](api["encode_cover"](pix))
    yield "cover", ["ok", [got[0], got[1], bytes(got[2]) == pix]]
    yield "cover_not", ["ok", api["decode_cover"](b"\x89PNG nope")]
    yield "cover_bad", _call(api["encode_cover"], b"\0" * 9)


def python_api(m):
    """The workload's mapping over a module carrying moy_carts' user-files
    names (the layer's own module, or anything speaking it)."""
    api = {n: getattr(m, n) for n in (
        "save_file", "load_file", "list_files", "count_files", "new_file_name",
        "free_file_name", "rename_file", "duplicate_file", "delete_file",
        "trash_list", "restore_file", "empty_trash", "prune_trash",
        "history_commit", "load_history", "prune_history", "clear_history",
        "content_sig", "stamp_provenance", "read_provenance", "project_kind",
        "file_path", "files_root", "vault_ext", "script_ext", "project_folder")}
    for name in ("encode_image", "decode_image", "encode_cover", "decode_cover"):
        api[name] = getattr(m, name)
    api["history_ops"] = m.history_ops
    hc = api["history_commit"]
    api["history_commit"] = lambda k, n, ops, kf, root: hc(k, n, ops, keyframe=kf, root=root)
    pt = api["prune_trash"]
    api["prune_trash"] = lambda root, keep: pt(root, keep=keep)
    ph = api["prune_history"]
    api["prune_history"] = lambda k, n, root, keep: ph(k, n, root, keep=keep)
    return api

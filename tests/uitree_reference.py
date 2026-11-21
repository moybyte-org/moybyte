"""What the Python reference engine drew, kept as records: a `test_uitree_c_*`
pair holds the C engine to the frames, states and traces the reference gave
for the same fixture and script before it was deleted (K13, 2026-11-21).

    held(test_file, key, value)

compares `value` (the C engine's run) with the record `key` in
tests/uitree_reference/<test file>.json and fails naming the key. A value is
kept plain where it is short (a trace, a state, a code) and as a digest where
it is long (frames are their pixels' digests). `MOYBYTE_RECORD_REFERENCE=1`
writes the value instead; it was set once, on the reference's half of each
pair, and a record is re-taken only for a cause named in its commit.
"""

import hashlib
import json
import os
from pathlib import Path

RECORDS = Path(__file__).resolve().parent / "uitree_reference"
RECORD_ENV = "MOYBYTE_RECORD_REFERENCE"
_PLAIN_MAX = 2000


def _plain(v):
    """`v` as JSON: a parity Frame is (step, digest, state); bytes a digest."""
    if hasattr(v, "digest") and hasattr(v, "state"):
        return [v.step, v.digest(), v.state]
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "sha256:" + hashlib.sha256(bytes(v)).hexdigest()
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return [[_plain(k), _plain(x)] for k, x in sorted(v.items(), key=lambda kv: repr(kv[0]))]
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return repr(v)


def _kept(v):
    p = _plain(v)
    s = json.dumps(p, sort_keys=True)
    return p if len(s) <= _PLAIN_MAX else "sha256:" + hashlib.sha256(s.encode()).hexdigest()


def _path(test_file):
    return RECORDS / (Path(test_file).stem + ".json")


def recorded(test_file, key):
    """The record `key` as it is kept (plain, or a digest)."""
    path = _path(test_file)
    recs = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    assert key in recs, "no reference record %r in %s" % (key, path.name)
    return recs[key]


def held(test_file, key, value):
    """Assert `value` is what the reference recorded under `key`."""
    path = _path(test_file)
    recs = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    got = _kept(value)
    if os.environ.get(RECORD_ENV):
        recs[key] = got
        RECORDS.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(recs, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return
    want = recorded(test_file, key)
    if got != want and isinstance(want, list) and isinstance(got, list) and len(got) == len(want):
        at = next(i for i, (a, b) in enumerate(zip(got, want)) if a != b)
        raise AssertionError("%s: %r differs from the reference's record at item %d: %r != %r"
                             % (path.name, key, at, got[at], want[at]))
    assert got == want, "%s: %r differs from the reference's record:\n  got  %r\n  kept %r" % (
        path.name, key, got, want)

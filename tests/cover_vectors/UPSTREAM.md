# moy-spec cover vectors — vendored

The PNG files and `expected.json` here are copied unmodified from
[moy-spec](https://github.com/moybyte-org/moy-spec)'s `conformance/covers/`
(its `build.py`, which writes them, stays there).

| | |
|---|---|
| vendored at | `7cf0434` (2026-10-02, branch covers) |

SPEC.md §3.6 is the cover profile, and these are the files a reader must
accept or ignore: `expected.json` gives each file's verdict, and for a cover
the sha256 of its 128 × 128 pixels as R, G, B bytes, row-major.
`tests/test_cover_png.py` hands every file to both of this repository's
readers — the native `moy_png` (`native/moy_png/`) and `runtime/cover_png.py`'s
`Reference` — on every `make test`.

## Re-vendoring

```sh
SPEC=../../moy-spec
cp $SPEC/conformance/covers/*.png $SPEC/conformance/covers/expected.json .
```

and update the commit above. A verdict that moves is a spec change to follow:
moy-spec's `moycore/cover.py` `read` is the reference reader, and both readers
here mirror it rule for rule.

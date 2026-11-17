# moy-spec format vectors — vendored

`format.json` is copied unmodified from
[moy-spec](https://github.com/moybyte-org/moy-spec)'s `conformance/format.json`.

| | |
|---|---|
| vendored at | `09b8c93` (format.json added there, 2026-11-17) |

SPEC.md §13 says how a host compares a manifest's `format` with its own level;
each case gives a `format`, the host's level and the verdict: `runs`, `newer`
(refused as a newer console's cart) or `not moy`. `tests/test_cart_format_level.py`
holds `runtime/run/player_fit.py`'s `_format_newer` to every case: a `newer`
case is refused, and every other runs, `not moy` included, because a `format`
that names no level is a Moybyte cart here.

## Re-vendoring

```sh
cp ../../moy-spec/conformance/format.json .
```

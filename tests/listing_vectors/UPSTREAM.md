# moy-spec listing vectors — vendored

`listing.json` is copied unmodified from
[moy-spec](https://github.com/moybyte-org/moy-spec)'s `conformance/listing.json`.

| | |
|---|---|
| vendored at | `a7908d8` (listing.json last changed in `8b02e1c`, 2026-10-17) |

SPEC.md §3.7 says how a host reads a manifest's `description`, `genre` and
`tags`; each case gives a manifest and what a reader must reach (`null` is
absent). `tests/test_cart_listing.py` hands every case to `runtime/cart_listing.py`,
to `moy_carts.load` (the manifest on disk) and to `cart_index.parse_index` (an
index entry) on every `make test`.

## Re-vendoring

```sh
cp ../../moy-spec/conformance/listing.json .
```

and update the commit above. A case that moves is a spec change to follow:
moy-spec's `moycore/cart.py` (`normalize_description`, `normalize_genre`,
`normalize_tags`) is the reference reader.

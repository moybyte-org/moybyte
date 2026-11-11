"""Moybyte's own manifest fields are one list: `moy_store_base.VENDOR_FIELDS`
names every field a reader takes from the manifest's "moybyte" object, and
docs/app_api_v1.md section 2's table documents exactly those. A field read but
not declared, or declared and documented with no reader, turns this red."""

import os
import re

from moy_store_base import VENDOR_FIELDS

ROOT = os.path.join(os.path.dirname(__file__), "..")

# The Python readers take a field off `ven`/`vendor(man)`; the C catalogue
# takes it with vfield() over the "moybyte" object.
_PY_READ = re.compile(r'\bven(?:dor\([a-z_]*\))?\.get\("([a-z_]+)"')
_C_READ = re.compile(r'\bvfield\(\w+, \w+, "([a-z_]+)"')


def _read_fields():
    found = set()
    for top in ("runtime", "device", "tools", "native/moy_store"):
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, top)):
            for name in files:
                pat = _PY_READ if name.endswith(".py") else (
                    _C_READ if name.endswith(".c") else None)
                if pat is None:
                    continue
                with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                    found.update(pat.findall(f.read()))
    return found


def _documented_fields():
    with open(os.path.join(ROOT, "docs", "app_api_v1.md"), encoding="utf-8") as f:
        text = f.read()
    start = text.index("Moybyte's fields go in ONE object")
    table = text[start:text.index("\n## ", start)]
    fields = set()
    for row in table.splitlines():
        if row.startswith("| `"):
            fields.update(re.findall(r"`([a-z_]+)`", row.split("|")[1]))
    return fields


def test_every_vendor_field_is_declared_read_and_documented():
    declared = set(VENDOR_FIELDS)
    assert len(declared) == len(VENDOR_FIELDS)
    assert _read_fields() == declared
    assert _documented_fields() == declared

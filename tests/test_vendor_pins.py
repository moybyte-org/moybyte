"""The vendored trees no script re-vendors are held to a hash pin.

Lua (native/moy_lua/lua) and the two DSI panel drivers (native/p4/moy_dsi's
vendor and vendor_jd9365) were copied once and change only by hand, each
change recorded in the copy's MODIFICATIONS.md. Their `<name>_vendor.json`
names the upstream and the sha256 of every file but that record; an edit the
record does not carry fails here until it is recorded and the pin re-stamped
with tools/vendor_stamp.py."""

import json
import os

import pytest

from tools import vendor_stamp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PINS = ["native/moy_lua/lua_vendor.json",
        "native/p4/moy_dsi/ek79007_vendor.json",
        "native/p4/moy_dsi/jd9365_vendor.json"]


def _pin(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("rel", PINS)
def test_the_copy_is_the_pinned_tree(rel):
    pin = _pin(rel)
    assert pin["files"] and pin["upstream"]["name"] and pin["upstream"]["url"]
    now = vendor_stamp.tree(pin["dir"])
    for path, want in sorted(pin["files"].items()):
        assert path in now, "%s is pinned but not on disk" % path
        assert now[path] == want, (
            "%s is not the pinned copy. Record the change in %s/%s, then "
            "re-stamp: python3 tools/vendor_stamp.py %s"
            % (path, pin["dir"], vendor_stamp.RECORD, rel))
    extra = sorted(set(now) - set(pin["files"]))
    assert not extra, "%s are in the copy but not its pin (%s)" % (extra, rel)


@pytest.mark.parametrize("rel", PINS)
def test_the_record_names_the_pinned_upstream(rel):
    pin = _pin(rel)
    with open(os.path.join(ROOT, pin["dir"], vendor_stamp.RECORD), encoding="utf-8") as f:
        record = f.read()
    up = pin["upstream"]
    assert up.get("version", "") in record
    assert up.get("commit", "") in record

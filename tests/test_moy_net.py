"""runtime/moy_net.py, the links twin (docs/kernel_survival_2026-10.md
section 6): the WiFi credential rules, the peer table, and the sync batch's
codec. (The HTTP parser and writer are pinned by tests/test_moy_webserver.py.)"""

import json

from runtime import moy_net, moy_spine


def test_an_empty_password_resolves_to_the_stored_one():
    assert moy_net.wifi_password("", "hunter2") == "hunter2"
    assert moy_net.wifi_password("new", "hunter2") == "new"
    assert moy_net.wifi_password("", None) == ""


def test_a_blank_password_the_radio_did_not_verify_is_never_remembered():
    assert moy_net.wifi_remember(True, "", None)            # associated
    assert moy_net.wifi_remember(False, "pw", "old")        # a new password
    assert not moy_net.wifi_remember(False, "", "old")      # blank, unverified
    assert not moy_net.wifi_remember(False, "old", "old")   # nothing new


def test_the_peer_table_reads_like_the_dict_it_replaced():
    t = moy_net.PeerTable()
    assert t == {} and len(t) == 0 and t.get(b"a") is None
    t[b"a"] = "Ann"
    t[b"b"] = "Bo"
    h = t.handle(b"a")
    assert (h >> moy_spine.KIND_SHIFT) & 0xF == moy_spine.KIND_PEER
    assert t[b"a"] == "Ann" and b"b" in t and len(t) == 2
    assert sorted(t.values()) == ["Ann", "Bo"] and sorted(t.keys()) == [b"a", b"b"]
    t[b"a"] = "Ann2"                                  # same row, new value
    assert t.handle(b"a") == h and t == {b"a": "Ann2", b"b": "Bo"}
    del t[b"a"]
    assert t.pop(b"b") == "Bo" and t.pop(b"b", 7) == 7 and t == {}


def test_the_batch_codec_round_trips_both_shapes():
    v1 = moy_net.encode_batch(1, None, [{"p": "x"}], None)
    assert json.loads(v1) == {"v": 1, "ops": [{"p": "x"}]}
    assert moy_net.decode_batch(v1) == (1, None, [{"p": "x"}], None)
    v2 = moy_net.encode_batch(2, "files", [], "1234")
    assert moy_net.decode_batch(v2.encode()) == (2, "files", [], "1234")
    for bad in (b"\xff", "not json", "[]"):
        assert moy_net.decode_batch(bad) is None

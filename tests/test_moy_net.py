"""native/moy_net's wire half (docs/kernel_survival_2026-10.md section 6) over
runtime/net_binding.py: the WiFi credential rules, the HTTP head parser and the
sync batch's envelope, and the C under the sanitizers. The radio link's peer
table is device/moy_espnow.py's. (The parser's transport use is pinned by
tests/test_moy_webhost.py.)"""

import json
import os
import shutil
import subprocess

import pytest

from device.moy_espnow import PeerTable
from runtime import moy_spine
from runtime import net_binding as moy_net

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET = os.path.join(ROOT, "native", "moy_net")
SPINE = os.path.join(ROOT, "native", "moy_spine")


def test_an_empty_password_resolves_to_the_stored_one():
    assert moy_net.wifi_password("", "hunter2") == "hunter2"
    assert moy_net.wifi_password("new", "hunter2") == "new"
    assert moy_net.wifi_password("", None) == ""
    assert moy_net.wifi_password(None, None) is None


def test_a_blank_password_the_radio_did_not_verify_is_never_remembered():
    assert moy_net.wifi_remember(True, "", None)            # associated
    assert moy_net.wifi_remember(False, "pw", "old")        # a new password
    assert moy_net.wifi_remember(False, "pw", None)         # nothing stored
    assert not moy_net.wifi_remember(False, "", "old")      # blank, unverified
    assert not moy_net.wifi_remember(False, "old", "old")   # nothing new


def test_the_peer_table_reads_like_the_dict_it_replaced():
    t = PeerTable()
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
    assert v2 == '{"v": 2, "root": "files", "ops": [], "pin": "1234"}'
    assert moy_net.decode_batch(v2.encode()) == (2, "files", [], "1234")
    for bad in (b"\xff", "not json", "[]", b'{"v": 1, "t": "\xed\xa0\x80"}'):
        assert moy_net.decode_batch(bad) is None


def test_a_repeated_field_reads_as_json_loads_keeps_it():
    assert moy_net.decode_batch('{"v": 1, "ops": [], "v": 3}') == (3, None, [], None)
    assert moy_net.decode_batch('{"\\u0076": 2, "ops": 7}') == (2, None, 7, None)


def test_the_head_parser_reads_bytes_and_keeps_the_last_length():
    raw = "GET /é?pin=1 HTTP/1.1\r\nContent-Length: 3\r\ncontent-length: 5\r\n\r\nbody!"
    m, t, clen, end = moy_net.parse_request(raw.encode())
    assert (m, t, clen) == ("GET", "/é?pin=1", 5)
    assert raw.encode()[end:] == b"body!", "header_end is a byte offset"
    assert moy_net.parse_request(b"GET  /x HTTP/1.1\n\n")[:2] == ("GET", "")
    assert moy_net.parse_request(b"NOSPACE\r\n\r\n") == (None, None, 0, -1)
    for v in (b"-3", b"1_0", b"x", b"99999999999"):
        assert moy_net.parse_request(b"P / H\r\nContent-Length: " + v + b"\r\n\r\n")[2] == 0
    assert moy_net.parse_request(b"P / H\r\nContent-Length: +7 \r\n\r\n")[2] == 7
    assert moy_net.parse_request(b"\xe9 /\xff H\r\n\r\n")[:2] == ("\xe9", "/\xff")


def test_the_response_names_its_status():
    r = moy_net.http_response(403, b"no", "text/plain")
    assert r.startswith(b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\n"
                        b"Content-Length: 2\r\n")
    assert r.endswith(b"Connection: close\r\n\r\nno")
    assert moy_net.http_response(299, "").startswith(b"HTTP/1.1 299 OK\r\n")


def test_the_fuzz_walk_holds_under_the_sanitizers(tmp_path):
    cc = shutil.which(os.environ.get("CC", "cc"))
    if cc is None:
        pytest.skip("no C compiler")
    exe = str(tmp_path / "fuzz_net")
    build = subprocess.run(
        [cc, "-std=c99", "-g", "-O1", "-fsanitize=address,undefined",
         "-fno-sanitize-recover=all", "-I", NET, "-I", SPINE,
         os.path.join(NET, "fuzz_net.c"), os.path.join(NET, "moy_http.c"),
         os.path.join(NET, "moy_sync.c"),
         os.path.join(NET, "moy_link.c"), os.path.join(NET, "moy_ota.c"),
         os.path.join(NET, "moy_gpio.c"), os.path.join(NET, "moy_dns.c"),
         os.path.join(SPINE, "moy_json.c"), "-o", exe],
        capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    out = subprocess.run([exe, "7", "60000"], capture_output=True, text=True,
                         timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]

"""The kernel spine's interface, pinned once for every binding: the C spine
(native/moy_spine, docs/kernel_spine_2026-10.md) and its Python oracle
(tests/spine_twin.py), each test unchanged.

`BINDINGS` names each implementation by its module: the Python oracle, and the
C spine over the host's C ABI through ctypes (tools/moy_spine_binding.py).
tests/test_moy_spine_twins.py runs this same file on the boards' VM over the
native module. The semantic trace in tests/test_semantic_traces.py replays one
session of this interface on every VM and pins the handle VALUES; this file
pins the rules:

  * a handle is gen << GEN_SHIFT | kind << KIND_SHIFT | slot: never 0, below
    2**30, the first row slot 0 at generation 1;
  * a handle another table made, a released one and a forged one raise
    StaleHandle (a ValueError) and valid() says False; a non-int raises
    TypeError; a freed slot is reused lowest-first under the next generation,
    which wraps from GEN_MAX to 1; a full table is OSError(ENOSPC);
  * an app id or a back-stack kind is a str of 1..ID_MAX bytes;
  * the back-stack's root is the launcher, goto answers STAYED / PUSHED /
    RETURNED, a kind is on it at most once;
  * a run's exit route follows its caller's kind; the app-return only sets;
  * a lease tag outside LEASE_TAGS is refused, a release of one never held
    is not;
  * settings rows hold JSON text and `set` is the one way in: it marks the
    store dirty and persists it through the save hook, a failed write leaves it
    dirty for the next, `get` decodes a row afresh, a load that is refused
    changes nothing, and the file is the bytes json.dumps writes.
"""

import json

import pytest

try:
    from tools import moy_spine_binding
except ImportError:         # the VM's run of this file has no tools/
    moy_spine_binding = None
if moy_spine_binding is None:
    # On a VM `runtime.moy_spine` is the one under test, native or the oracle
    # (tests/vm_suite.py puts it there).
    from runtime import moy_spine
    BINDINGS = {"vm": moy_spine}
else:
    import spine_twin
    BINDINGS = {"python": spine_twin}
    BINDINGS.update(moy_spine_binding.host_bindings())


@pytest.fixture(params=sorted(BINDINGS))
def sp(request):
    return BINDINGS[request.param]


# -- handle tables ----------------------------------------------------------

def _h(sp, kind, slot, gen):
    return (gen << sp.GEN_SHIFT) | (kind << sp.KIND_SHIFT) | slot


def test_a_handle_is_kind_slot_and_generation(sp):
    t = sp.Table(3, "thing")
    a, b = t.new("a"), t.new("b")
    assert (a, b) == (_h(sp, 3, 0, 1), _h(sp, 3, 1, 1))
    assert t.get(a) == "a" and t.get(b) == "b" and t.count() == 2
    # the generation sits where the store index's does
    from runtime import moy_index
    assert sp.GEN_SHIFT == moy_index.SLOT_BITS and sp.GEN_MAX == moy_index.GEN_MAX


# The kinds of moy_htab.h, one per client table: sprint 3's carve added all
# but APP (docs/kernel_survival_2026-10.md section 3.1), and its audio pass
# CLIP, the sample voice's clips.
KINDS = {"APP": 1, "BUF": 2, "CANVAS": 3, "SURF": 4, "OWNER": 5, "SRC": 6,
         "PEER": 7, "AUDIO": 8, "CLIP": 9}


def test_the_kinds_are_the_headers(sp):
    for name, kind in KINDS.items():
        assert getattr(sp, "KIND_" + name) == kind, name
    try:
        src = open(__file__.rsplit("/", 2)[0]
                   + "/native/moy_spine/moy_htab.h").read()
    except (NameError, OSError):     # a VM run: no __file__, no tree beside it
        return
    for name, kind in KINDS.items():
        assert "MOY_KIND_%s = %d," % (name, kind) in src, name


def test_a_released_or_foreign_handle_is_refused_loudly(sp):
    t, u = sp.Table(1, "app"), sp.Table(2, "buf")
    h = t.new("x")
    assert not u.valid(h)
    with pytest.raises(sp.StaleHandle, match="stale buf handle"):
        u.get(h)                         # same slot and generation, other table
    assert t.release(h) == "x"
    for fn in (t.get, t.release, lambda x: t.put(x, 1)):
        with pytest.raises(sp.StaleHandle):
            fn(h)
    assert not t.valid(h)
    for forged in (0, -1, _h(sp, 1, 9, 1), _h(sp, 1, 0, sp.GEN_MAX + 1)):
        assert not t.valid(forged)
        with pytest.raises(ValueError):
            t.get(forged)
    for junk in (None, "1", 1.0):
        assert not t.valid(junk)
        with pytest.raises(TypeError):
            t.get(junk)


def test_a_freed_slot_is_reused_lowest_first_under_its_next_generation(sp):
    t = sp.Table(1, "app", 4)
    hs = [t.new(i) for i in range(4)]
    with pytest.raises(OSError) as e:
        t.new(9)
    assert e.value.args[0] == 28
    t.release(hs[2])
    t.release(hs[1])
    assert t.new("again") == _h(sp, 1, 1, 2)
    assert t.handles() == [hs[0], _h(sp, 1, 1, 2), hs[3]]


def test_the_generation_wraps_to_one(sp):
    t = sp.Table(1, "app", 1)
    for _ in range(sp.GEN_MAX - 1):
        t.release(t.new(0))
    h = t.new(0)
    assert h >> sp.GEN_SHIFT == sp.GEN_MAX and h < (1 << 30)
    t.release(h)
    assert t.new(0) >> sp.GEN_SHIFT == 1


def test_a_table_takes_a_kind_and_a_size_it_can_name(sp):
    for kind, slots in ((0, 4), (16, 4), (1, 0), (1, sp.SLOTS + 1)):
        with pytest.raises(ValueError):
            sp.Table(kind, "x", slots)
    with pytest.raises(TypeError):
        sp.Table(1, 5)


def test_a_table_holds_any_object_and_gives_it_back(sp):
    t = sp.Table(2, "buf")
    rows = ["s", 3, None, (1, 2), [4], {"k": 5}]
    hs = [t.new(r) for r in rows]
    assert [t.get(h) for h in hs] == rows and t.count() == len(rows)
    t.put(hs[0], "t")
    assert t.release(hs[0]) == "t" and t.release(hs[2]) is None
    assert t.handles() == [hs[1], hs[3], hs[4], hs[5]]


# -- the app registry -------------------------------------------------------

def test_the_registry_keeps_ids_and_metadata_not_objects(sp):
    reg = sp.AppRegistry()
    h = reg.register("files", "Files", True, [310, 230])
    assert reg.find("files") == h and reg.find("paint") == 0
    assert (reg.app_id(h), reg.title(h), reg.text_mode(h), reg.min_size(h)) == (
        "files", "Files", True, (310, 230))
    g = reg.register("calc", "CALC")
    assert reg.min_size(g) is None and reg.text_mode(g) is False
    assert reg.handles() == [h, g] and reg.count() == 2
    with pytest.raises(ValueError, match="duplicate app id: files"):
        reg.register("files", "again")
    with pytest.raises(TypeError):
        reg.register(None, "x")
    with pytest.raises(TypeError):
        reg.find(7)
    for bad in ("", "x" * (sp.ID_MAX + 1)):
        with pytest.raises(ValueError):
            reg.register(bad, "x")
    with pytest.raises(sp.StaleHandle, match="stale app handle"):
        reg.title(h + (1 << sp.GEN_SHIFT))


def test_a_min_size_is_two_ints_that_fit_32_bits(sp):
    reg = sp.AppRegistry()
    h = reg.register("a", "A", False, (-5, (1 << 31) - 1))
    assert reg.min_size(h) == (-5, (1 << 31) - 1)
    for bad in ((1 << 31, 1), (1, -(1 << 31) - 1), (1 << 40, 1)):
        with pytest.raises(ValueError):
            reg.register("b", "B", False, bad)
    assert reg.find("b") == 0 and reg.count() == 1
    assert reg.title(reg.register("c", 42)) == "42"     # a title is str()'d
    assert reg.title(reg.register("d", "\u00e9t\u00e9")) == "\u00e9t\u00e9"


# -- the back-stack ---------------------------------------------------------

def test_goto_pushes_returns_and_stays(sp):
    st = sp.BackStack()
    assert st.kinds() == ["launcher"] and st.top() == "launcher"
    assert st.goto("launcher") == sp.STAYED
    assert st.goto("menu") == sp.PUSHED
    assert st.goto("desktop") == sp.PUSHED
    assert st.goto("settings") == sp.PUSHED
    assert st.goto("menu") == sp.RETURNED
    assert st.kinds() == ["launcher", "menu"] and st.depth() == 2
    assert st.has("menu") and not st.has("desktop")
    assert st.index("menu") == 1 and st.index("desk") == -1
    assert st.goto("launcher") == sp.RETURNED and st.kinds() == ["launcher"]


def test_remove_takes_one_kind_and_never_the_root(sp):
    st = sp.BackStack()
    for k in ("desk", "menu", "desktop", "settings"):
        st.goto(k)
    assert st.remove("desktop") is True
    assert st.kinds() == ["launcher", "desk", "menu", "settings"]
    assert st.remove("desktop") is False
    assert st.remove("launcher") is False and st.top() == "settings"


def test_the_back_stack_refuses_what_it_cannot_hold(sp):
    st = sp.BackStack()
    for bad, exc in ((None, TypeError), ("", ValueError),
                     ("x" * (sp.ID_MAX + 1), ValueError)):
        with pytest.raises(exc):
            st.goto(bad)
    for i in range(st.DEPTH - 1):
        st.goto("k%d" % i)
    with pytest.raises(OSError) as e:
        st.goto("one-more")
    assert e.value.args[0] == 28 and st.depth() == st.DEPTH


# -- the return records -----------------------------------------------------

def test_an_exit_routes_by_its_callers_kind(sp):
    reg = sp.AppRegistry()
    reg.register("files", "Files")
    rt = sp.Returns(reg)
    for caller, route in ((sp.EDITOR, sp.ROUTE_EDITOR), ("files", sp.ROUTE_APP),
                          ("launcher", sp.ROUTE_HOME), (None, sp.ROUTE_HOME)):
        rt.run(caller)
        assert rt.route(False) == route
        assert rt.route(True) == sp.ROUTE_WINDOW
        assert rt.caller() == caller            # route spends nothing
        assert rt.spend() == caller and rt.caller() is None
    with pytest.raises(TypeError):
        rt.run(object())


def test_the_app_return_only_sets_and_only_for_an_app(sp):
    reg = sp.AppRegistry()
    reg.register("files", "Files")
    rt = sp.Returns(reg)
    assert rt.note("menu") is False and rt.back() is None
    assert rt.note("files") is True and rt.back() == "files"
    assert rt.note("settings") is False and rt.back() == "files"
    rt.run("menu")
    rt.spend()
    assert rt.back() == "files"                 # it survives a nested run
    assert rt.take_back() == "files" and rt.back() is None


# -- WiFi leases ------------------------------------------------------------

def test_the_lease_tags_are_the_closed_set(sp):
    assert tuple(sp.LEASE_TAGS) == ("web", "update", "settings", "cart", "link",
                                    "carts", "dev")
    ls = sp.Leases()
    for i, tag in enumerate(sp.LEASE_TAGS):
        assert ls.hold(tag) == (1 << (i + 1)) - 1 and ls.held(tag)
    assert ls.holders() == list(sp.LEASE_TAGS)


def test_leases_are_a_mask_over_a_closed_set_of_tags(sp):
    ls = sp.Leases()
    assert ls.hold("update") == ls.hold("update") != 0
    ls.hold("web")
    assert ls.holders() == ["web", "update"] and ls.held("web")
    assert ls.release("update") != 0
    assert ls.release("link") == ls.mask()       # never held: not an error
    assert ls.release("web") == 0 and ls.holders() == []
    with pytest.raises(ValueError):
        ls.hold("wasm")
    with pytest.raises(ValueError):
        ls.release("nobody")
    with pytest.raises(TypeError):
        ls.held(None)


def test_the_kernels_tables_outlive_their_views(sp):
    """kernel(fresh): the console's tables as the kernel's own
    (docs/kernel_cartpath_2026-10.md section 5.4) -- one set, made once, which
    a view dropping (a VM stop's sweep) does not free. A return start
    (fresh False) reads the route and the lease the stopped VM left; every
    start clears the registry, and any other start puts the rest back."""
    apps, back, returns, leases = sp.kernel(True)
    apps.register("files", "Files")
    back.goto("desktop")
    returns.run("menu")
    leases.hold("dev")
    del apps, back, returns, leases
    import gc
    gc.collect()
    apps, back, returns, leases = sp.kernel(False)
    assert back.kinds() == ["launcher", "desktop"]
    assert returns.caller() == "menu"
    assert leases.holders() == ["dev"]
    assert apps.count() == 0 and apps.find("files") == 0
    assert apps.register("files", "Files") != 0
    apps, back, returns, leases = sp.kernel(True)
    assert back.kinds() == ["launcher"] and returns.caller() is None
    assert leases.mask() == 0 and apps.count() == 0


# -- the settings store -----------------------------------------------------

def test_settings_rows_hold_json_text_and_dump_the_file(sp):
    s = sp.Settings()
    assert s.load('{"theme": "outline", "favorites": ["/a.moy"], "fs": 2}') == 3
    assert sorted(s.keys()) == ["favorites", "fs", "theme"]
    assert s.text("theme") == '"outline"' and s.text("nope") is None
    assert s.get("theme") == "outline" and s.get("favorites") == ["/a.moy"]
    assert s.get("fs") == 2 and s.get("nope") is None and s.get("nope", 7) == 7
    s.set("fs", 3)
    s.set_text("guard", '{"open": null}')
    assert s.delete("theme") is True and s.delete("theme") is False
    assert json.loads(s.dump()) == {"favorites": ["/a.moy"], "fs": 3,
                                    "guard": {"open": None}}
    with pytest.raises(ValueError):
        s.set_text("fs", "not json")
    with pytest.raises(TypeError):
        s.set_text("fs", 3)
    with pytest.raises(ValueError):
        s.load("[1, 2]")
    with pytest.raises(ValueError):
        s.get("")
    with pytest.raises(ValueError):
        s.set("", 1)
    with pytest.raises(TypeError):
        s.set(None, 1)
    with pytest.raises(TypeError):
        s.text(3)


def test_a_value_decodes_to_what_json_loads_makes_of_it(sp):
    s = sp.Settings()
    values = [None, True, False, 0, -7, 123456789, 2 ** 40, 1.5, "", "plain",
              "q\"uote\n", "caf\u00e9", [], {}, [1, [2, {"a": None}]],
              {"k": [True, "x"], "n": {"m": -1}}]
    for i, v in enumerate(values):
        s.set("k%d" % i, v, False)
    for i, v in enumerate(values):
        got = s.get("k%d" % i)
        assert got == v and type(got) is type(v), (i, v, got)
    s.set_text("padded", " 7 ", False)
    s.set_text("neg", "-0", False)
    assert s.get("padded") == 7 and s.get("neg") == 0 and s.text("padded") == " 7 "


def test_a_row_is_decoded_afresh_so_a_caller_cannot_change_it(sp):
    s = sp.Settings()
    s.set("l", [1, [2], {"a": 3}])
    got = s.get("l")
    got.append(9)
    got[1].append(8)
    got[2]["b"] = 7
    assert s.get("l") == [1, [2], {"a": 3}] and s.text("l") == '[1, [2], {"a": 3}]'


def test_a_write_marks_the_store_dirty_and_persists_it(sp):
    wrote = []
    s = sp.Settings(lambda text: wrote.append(text))
    assert not s.dirty() and s.flush() is True and wrote == []
    s.set("fs", 3)
    assert wrote == ['{"fs": 3}'] and not s.dirty()
    s.set("theme", "outline")
    s.set("fs", 2)
    assert wrote[1:] == ['{"fs": 3, "theme": "outline"}',
                         '{"fs": 2, "theme": "outline"}']
    assert s.delete("theme") is True
    assert wrote[-1] == '{"fs": 2}' and len(wrote) == 4
    assert s.delete("theme") is False and len(wrote) == 4      # nothing to write
    s.set_text("raw", " [1] ")
    assert wrote[-1] == '{"fs": 2, "raw":  [1] }'
    assert not s.dirty() and s.flush() is True and len(wrote) == 5


def test_a_failed_write_leaves_the_store_dirty_until_one_lands(sp):
    landed = [False]
    wrote = []

    def save(text):
        wrote.append(text)
        return landed[0]

    s = sp.Settings(save)
    s.set("a", 1)
    assert s.dirty() and wrote == ['{"a": 1}']
    s.set("b", 2)                       # the next write carries the first
    assert s.dirty() and wrote[-1] == '{"a": 1, "b": 2}'
    assert s.flush() is False and len(wrote) == 3
    landed[0] = True
    assert s.flush() is True and not s.dirty() and len(wrote) == 4
    assert s.flush() is True and len(wrote) == 4
    none = sp.Settings()                # no hook: nowhere to write, still dirty
    none.set("a", 1)
    assert none.dirty() and none.flush() is False and none.get("a") == 1


def test_persist_false_defers_the_write_to_the_next_one(sp):
    wrote = []
    s = sp.Settings(lambda text: wrote.append(text))
    s.set("a", 1, persist=False)
    s.set_text("b", "2", False)
    assert wrote == [] and s.dirty()
    s.set("c", 3)
    assert wrote == ['{"a": 1, "b": 2, "c": 3}'] and not s.dirty()
    assert s.delete("a", persist=False) is True
    assert s.dirty() and len(wrote) == 1
    assert s.flush() is True and wrote[-1] == '{"b": 2, "c": 3}'


def test_a_load_or_an_adopt_is_the_file_so_the_store_is_clean(sp):
    s = sp.Settings(lambda text: True)
    s.set("a", 1, False)
    assert s.dirty()
    s.load('{"b": 2}')
    assert not s.dirty() and s.keys() == ["b"]
    s.set("c", 3, False)
    s.adopt({"d": 4})
    assert not s.dirty() and s.keys() == ["d"]
    s.set("e", 5, False)
    with pytest.raises(ValueError):
        s.load("[1]")
    assert s.dirty() and s.keys() == ["d", "e"]       # a refused load changes nothing


# What `json.dumps` writes for these rows, spelled out: the format of system.json
# is these bytes, in every binding, on every VM.
GOLDEN = ('{"theme": "outline", "theme_variant": "dark", "font_scale": 2, '
          '"favorites": ["/carts/a.moy", "/carts/b.moy"], "desk_mru": [], '
          '"crisp_pixels": true, "ota_channel": "stable", '
          '"app_guard": {"strikes": {"files": 1}, "open": "files"}, '
          '"name": "Moy", "volume": 3, "skin": null, "ratio": 0.5}')


def test_the_file_is_the_bytes_json_dumps_writes(sp):
    wrote = []
    s = sp.Settings(lambda text: wrote.append(text))
    s.load(GOLDEN)
    assert s.dump() == GOLDEN                       # read and written back unchanged
    s.set("theme", "outline")                       # the same value: the same file
    assert wrote[-1] == GOLDEN
    s.set("font_scale", 3)                          # in place, in its position
    s.set("brand_new", {"a": [1, None]})            # a new key goes last
    s.delete("desk_mru")
    s.set("favorites", ["/carts/b.moy"])
    assert wrote[-1] == (
        '{"theme": "outline", "theme_variant": "dark", "font_scale": 3, '
        '"favorites": ["/carts/b.moy"], '
        '"crisp_pixels": true, "ota_channel": "stable", '
        '"app_guard": {"strikes": {"files": 1}, "open": "files"}, '
        '"name": "Moy", "volume": 3, "skin": null, "ratio": 0.5, '
        '"brand_new": {"a": [1, null]}}')
    t = sp.Settings()                               # and it reads back as written
    assert t.load(wrote[-1]) == 12 and t.dump() == wrote[-1]


def test_a_refused_load_changes_nothing(sp):
    s = sp.Settings()
    assert s.load('{"a": 1, "b": [2, 3]}') == 2
    for bad in ("", "nope", "[1, 2]", "3", '{"a": 1', '{"": 1}', "{'a': 1}",
                '{"a": 1} x'):
        with pytest.raises(ValueError):
            s.load(bad)
        assert s.dump() == '{"a": 1, "b": [2, 3]}'
    assert s.load("{}") == 0 and s.keys() == [] and s.dump() == "{}"


def test_settings_keys_hold_escapes_and_unicode(sp):
    s = sp.Settings()
    assert s.load('{"caf\\u00e9": 1, "a\\"b": 2, "n\\nl": 3, "\u00fc": 4}') == 4
    assert sorted(s.keys()) == sorted(["caf\u00e9", 'a"b', "n\nl", "\u00fc"])
    assert s.text("caf\u00e9") == "1" and s.get('a"b') == 2
    assert json.loads(s.dump()) == {"caf\u00e9": 1, 'a"b': 2, "n\nl": 3,
                                    "\u00fc": 4}
    s.set("tab\there", [])
    assert json.loads(s.dump())["tab\there"] == []


def test_a_value_nests_at_most_31_containers(sp):
    s = sp.Settings()
    s.set_text("k", "[" * 31 + "]" * 31)
    with pytest.raises(ValueError):
        s.set_text("k2", "[" * 32 + "]" * 32)
    with pytest.raises(ValueError):
        s.set("k3", json.loads("[" * 32 + "]" * 32))
    assert s.keys() == ["k"]
    assert s.load('{"a": ' + "[" * 31 + "]" * 31 + "}") == 1
    with pytest.raises(ValueError):
        s.load('{"a": ' + "[" * 32 + "]" * 32 + "}")
    assert s.keys() == ["a"]


def test_adopt_encodes_a_dict_into_rows(sp):
    s = sp.Settings()
    s.set("old", 1)
    d = {"b": [1, {"x": None}], "a": "caf\u00e9", "n": 2, "f": True}
    s.adopt(d)
    assert sorted(s.keys()) == ["a", "b", "f", "n"]   # a dict has no order on the VM
    assert s.text("b") == '[1, {"x": null}]' and s.get("n") == 2
    assert json.loads(s.dump()) == d
    with pytest.raises(ValueError):
        s.adopt({"ok": 1, "": 2})
    with pytest.raises(TypeError):
        s.adopt({1: 2})
    assert json.loads(s.dump()) == d                  # a refused adopt changes nothing


def test_system_json_on_the_card_is_byte_identical(tmp_path):
    """The format's pin through the shell's store (no migration, CLAUDE.md): a
    card written the way json.dumps wrote it reads, and writes back, as the same
    bytes, and every change through the one setter leaves the file json.dumps
    of the same dict would."""
    from ws_helpers import build_ws
    from runtime import moy_carts

    ws = build_ws(tmp_path)
    path = moy_carts.system_store_path(ws.carts_root)
    old = json.loads(GOLDEN)
    moy_carts.save_system(old, ws.carts_root)       # json.dumps(dict): the old writer
    with open(path) as f:
        assert f.read() == GOLDEN
    ws.prefs.load()
    ws.system.set("theme", "outline")               # a write that changes nothing
    with open(path) as f:
        assert f.read() == GOLDEN
    for key, value in (("font_scale", 3), ("brand_new", {"a": [1, None]}),
                       ("favorites", ["/carts/b.moy"]), ("theme_variant", "light")):
        ws.system.set(key, value)
        old[key] = value
        with open(path) as f:
            assert f.read() == json.dumps(old), key
    ws.system.delete("desk_mru")
    del old["desk_mru"]
    with open(path) as f:
        assert f.read() == json.dumps(old)
    assert moy_carts.load_system(ws.carts_root) == old


def test_a_write_through_the_setter_reaches_the_card_with_no_persist_call(tmp_path):
    from ws_helpers import build_ws
    from runtime import moy_carts

    ws = build_ws(tmp_path)
    ws.system.set("crisp_pixels", True)
    assert moy_carts.load_system(ws.carts_root)["crisp_pixels"] is True
    ws.system.set("steady", False, persist=False)
    assert "steady" not in moy_carts.load_system(ws.carts_root)     # deferred...
    ws.system.set("name", "Zed")
    assert moy_carts.load_system(ws.carts_root)["steady"] is False   # ...not lost


"""device/mem_census.py: the census's walkers, held to what they report.

The board half of tools/mem_census.py runs only on glass, where its figures
come from `gc`, `esp32` and `moy_alloc`. Its logic -- which buffer a path
reaches first, the order the destructive cut drops things in, what a mark
records -- is pinned here over fakes of those three.
"""

import types

import pytest

import mem_census as mc


class _View:
    """A stand-in for an off-heap memoryview: the walker matches by address."""

    def __init__(self, addr):
        self.addr = addr


class _FakeAlloc:
    def __init__(self, live):
        self._live = live

    def live(self):
        return list(self._live)

    def stats(self):
        return (len(self._live), sum(n for _a, n in self._live))


@pytest.fixture
def fake(monkeypatch):
    def install(live):
        monkeypatch.setattr(mc, "_moy_alloc", _FakeAlloc(live))
        monkeypatch.setattr(mc, "_addr", lambda v: getattr(v, "addr", None))
        # A view is what the walker checks by address; on a board that is
        # a memoryview, here the stand-in.
        monkeypatch.setattr(mc, "memoryview", _View, raising=False)
    return install


def test_offheap_names_each_buffer_by_the_first_path_and_keeps_the_rest(
        fake, monkeypatch):
    fake([(100, 4096), (200, 153600), (300, 77)])
    pool_mod = types.ModuleType("device_canvas")
    pool_mod._KEEP = {153600: [_View(200)]}

    class Ws:
        pass
    ws = Ws()
    ws.covers = types.SimpleNamespace(_buf=_View(100))
    ws.again = [_View(200)]           # the pool's buffer, reached second
    got = mc.offheap(ws, modules={"device_canvas": pool_mod,
                                  "other": types.ModuleType("other")})
    assert got["total"] == (3, 4096 + 153600 + 77)
    owners = dict(got["owners"])
    assert owners == {"device_canvas._KEEP[153600][0]": 153600,
                      "ws.covers._buf": 4096}
    assert got["unowned"] == [(300, 77)]


def test_offheap_is_absent_without_the_registry_walk(monkeypatch):
    monkeypatch.setattr(mc, "_moy_alloc", None)
    assert mc.offheap(object()) is None


class _FakeGc:
    """mem_alloc() is what the objects still 'held' sum to."""

    def __init__(self, held):
        self.held = held

    def collect(self):
        pass

    def mem_alloc(self):
        return sum(self.held.values())


def test_cut_drops_late_attributes_last_and_charges_each_drop(monkeypatch):
    held = {"launcher": 300, "carts": 500, "audio": 40, "zz": 7}
    fgc = _FakeGc(held)
    monkeypatch.setattr(mc, "gc", fgc)

    class Ws:
        def __setattr__(self, k, v):
            if v is None:
                held.pop(k, None)
            object.__setattr__(self, k, v)
    ws = Ws()
    for k in held:
        object.__setattr__(ws, k, k)
    rows = mc.cut(ws, modules={})
    names = [r[0] for r in rows]
    assert names == ["start", "ws.audio", "ws.zz", "ws.launcher", "ws.carts",
                     "left"]
    assert [r[1] for r in rows] == [847, 40, 7, 300, 500, 0]
    assert mc.CUTS is rows


def test_mark_is_inert_unarmed_and_records_a_row_armed(monkeypatch):
    monkeypatch.setattr(mc, "MARKS", None)
    mc.mark("x")
    assert mc.marks() is None
    fgc = _FakeGc({"a": 10})
    monkeypatch.setattr(mc, "gc", fgc)
    monkeypatch.setattr(mc, "MARKS", [])
    monkeypatch.setattr(mc, "MODE", "lite")
    mc.mark("store")
    (row,) = mc.marks()
    tag, t, areas, held, alloc, live, ps_free, ps_big, reg, amap = row
    assert (tag, alloc, live, amap) == ("store", 10, None, None)
    # A host has no gc.areas() and no esp32: absent, never 0.
    assert (areas, held, ps_free, ps_big) == (None, None, None, None)


def test_the_boot_flag_arms_only_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "FLAG", str(tmp_path / "mem_census"))
    assert mc._armed() is None
    (tmp_path / "mem_census").write_text("live")
    assert mc._armed() == "live"
    (tmp_path / "mem_census").write_text("")
    assert mc._armed() == "lite"


class _Klass:
    pass


def test_referrers_walk_containers_out_to_the_owning_instance(monkeypatch):
    """buffer <- memoryview <- raw map table <- instance of a named class;
    a second chain ends where no heap block holds the next link."""
    graph = {
        0x100: [(0x200, 32, id(memoryview)), (0x900, 16, id(memoryview))],
        0x200: [(0x300, 64, 0xdead)],                 # a raw table
        0x300: [(0x400, 32, id(_Klass))],
        0x900: [],
    }
    fgc = types.SimpleNamespace(refs=lambda a: tuple(graph.get(a, ())))
    monkeypatch.setattr(mc, "gc", fgc)
    mod = types.ModuleType("owner")
    mod._Klass = _Klass
    monkeypatch.setattr(mc, "_type_names",
                        lambda modules=None: {id(memoryview): "memoryview",
                                              id(_Klass): "owner._Klass"})
    chains = mc.referrers(0x100)
    ends = sorted(c[-1] if isinstance(c[-1], str) else c[-1][2] for c in chains)
    assert ends == ["none", "owner._Klass"]
    full = [c for c in chains if c[-1] != "none"][0]
    assert [k for _b, _n, k in full] == ["memoryview", "raw", "owner._Klass"]


def test_referrers_is_absent_without_gc_refs(monkeypatch):
    monkeypatch.setattr(mc, "gc", types.SimpleNamespace())
    assert mc.referrers(1) is None


def test_store_reads_the_native_index_and_none_without(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "moy_index",
                        types.SimpleNamespace(mem=lambda: (300, 500)))
    monkeypatch.setitem(sys.modules, "moy_catalogue",
                        types.SimpleNamespace(rows=lambda: 7))
    assert mc.store() == {"rows": 7, "bytes": 300, "high": 500}
    monkeypatch.setitem(sys.modules, "moy_index", types.SimpleNamespace())
    assert mc.store() is None

"""The battery's gauge (device/battery.py) and the console's `battery_level`
over it: a percent where a board reads a cell, None where it reads none."""

import importlib.util
from pathlib import Path

from ws_helpers import build_ws

_SPEC = importlib.util.spec_from_file_location(
    "battery", Path(__file__).resolve().parent.parent / "device" / "battery.py")
battery = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(battery)


class _Adc:
    """An ADC pin at `uv` microvolts; counts its reads."""

    def __init__(self, uv):
        self.uv = uv
        self.reads = 0

    def read_uv(self):
        self.reads += 1
        return self.uv


def test_the_curve_runs_from_empty_to_full_and_no_cell_is_none():
    assert battery.percent(4200) == 100 and battery.percent(4350) == 100
    assert battery.percent(4637) is None                   # the charger's rail, no cell
    assert battery.percent(3300) == 0 and battery.percent(3000) == 0
    assert battery.percent(3725) == 32                     # between two points
    assert battery.percent(battery.NO_CELL_MV - 1) is None
    levels = [battery.percent(mv) for mv in range(3300, 4201, 10)]
    assert levels == sorted(levels)


def test_the_gauge_reads_the_cell_through_its_divider_and_holds_it_a_while():
    adc = _Adc(1_900_000)                                  # 1.9 V at the pin
    gauge = battery.Battery(4, 2, adc=adc)
    assert gauge.millivolts() == 3800
    assert gauge.level() == 55
    reads = adc.reads
    adc.uv = 2_100_000
    assert gauge.level() == 55 and adc.reads == reads      # held: per-frame asks are free


def test_a_gauge_that_will_not_read_is_none():
    class _Dead:
        def read_uv(self):
            raise OSError("no adc")
    assert battery.Battery(4, 2, adc=_Dead()).level() is None
    assert battery.Battery(4, 2, adc=_Adc(0)).level() is None   # nothing connected


def test_the_console_answers_the_gauge_or_none(tmp_path):
    ws = build_ws(tmp_path)
    assert ws.battery_level() is None                      # the host: no gauge
    ws.battery = battery.Battery(4, 2, adc=_Adc(2_100_000))
    assert ws.battery_level() == 100

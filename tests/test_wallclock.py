"""The wall clock (runtime/system/wallclock.py, native/moy_net's clock):
the POSIX TZ conversion, the Time zone setting, the unsynced and synced
states as the bar shows them, and the time a certificate's dates are read
against (docs/os_2026-11/hardware.md section 1)."""

import calendar
import datetime
import time
import zoneinfo

import pytest

from runtime import wallclock as wc
from ws_helpers import build_ws

# Each picker zone's IANA name: the rules its TZ string must agree with.
IANA = {
    "Honolulu": "Pacific/Honolulu", "Anchorage": "America/Anchorage",
    "Los Angeles": "America/Los_Angeles", "Phoenix": "America/Phoenix",
    "Denver": "America/Denver", "Mexico City": "America/Mexico_City",
    "Chicago": "America/Chicago", "New York": "America/New_York",
    "Bogota, Lima": "America/Bogota", "Halifax": "America/Halifax",
    "Santiago": "America/Santiago", "Sao Paulo, Buenos Aires": "America/Sao_Paulo",
    "UTC": "UTC", "London, Dublin": "Europe/London", "Lisbon": "Europe/Lisbon",
    "Lagos": "Africa/Lagos", "Paris, Berlin, Belgrade": "Europe/Belgrade",
    "Johannesburg": "Africa/Johannesburg", "Cairo": "Africa/Cairo",
    "Athens, Helsinki, Kyiv": "Europe/Athens", "Istanbul": "Europe/Istanbul",
    "Moscow": "Europe/Moscow", "Nairobi": "Africa/Nairobi", "Tehran": "Asia/Tehran",
    "Dubai": "Asia/Dubai", "Karachi": "Asia/Karachi", "Delhi, Mumbai": "Asia/Kolkata",
    "Kathmandu": "Asia/Kathmandu", "Dhaka": "Asia/Dhaka",
    "Bangkok, Jakarta": "Asia/Bangkok", "Singapore": "Asia/Singapore",
    "Beijing, Shanghai": "Asia/Shanghai", "Perth": "Australia/Perth",
    "Tokyo": "Asia/Tokyo", "Seoul": "Asia/Seoul", "Adelaide": "Australia/Adelaide",
    "Brisbane": "Australia/Brisbane", "Sydney, Melbourne": "Australia/Sydney",
    "Auckland": "Pacific/Auckland",
}

BELGRADE = "CET-1CEST,M3.5.0,M10.5.0/3"


def _at(*ymdhms):
    return calendar.timegm(ymdhms + (0,) * (6 - len(ymdhms)))


@pytest.fixture(autouse=True)
def _utc_after():
    yield
    wc.set_zone(wc.UTC)
    wc._kernel[:] = [None, False]


# -- the conversion ------------------------------------------------------------------

def test_every_picker_zone_agrees_with_its_iana_rules_hour_by_hour():
    assert sorted(p for p, _tz in wc.ZONES) == sorted(IANA)
    start = _at(2026, 1, 1)
    for place, tz in wc.ZONES:
        assert wc.set_zone(tz), tz
        z = zoneinfo.ZoneInfo(IANA[place])
        for t in range(start, start + 3 * 366 * 86400, 3600 * 7 + 60):
            want = datetime.datetime.fromtimestamp(t, z).utcoffset().total_seconds()
            assert wc.offset(t) == want, (place, datetime.datetime.utcfromtimestamp(t))


def test_a_summer_transition_turns_on_its_second():
    wc.set_zone(BELGRADE)
    # 2026-03-29 01:00 UTC is 02:00 CET, the moment it becomes 03:00 CEST
    t = _at(2026, 3, 29, 1)
    assert wc.offset(t - 1) == 3600 and wc.offset(t) == 7200
    # 2026-10-25 01:00 UTC is 03:00 CEST, back to 02:00 CET
    t = _at(2026, 10, 25, 1)
    assert wc.offset(t - 1) == 7200 and wc.offset(t) == 3600
    assert tuple(wc.localtime(_at(2026, 7, 1, 12, 30))[:6]) == (2026, 7, 1, 14, 30, 0)


def test_the_southern_summer_spans_the_new_year():
    wc.set_zone("AEST-10AEDT,M10.1.0,M4.1.0/3")
    assert wc.offset(_at(2026, 1, 15)) == 11 * 3600
    assert wc.offset(_at(2026, 7, 15)) == 10 * 3600
    assert wc.offset(_at(2026, 12, 31, 23)) == 11 * 3600


@pytest.mark.parametrize("tz, east, summer", [
    ("UTC0", 0, None),
    ("<+0545>-5:45", 5 * 3600 + 45 * 60, None),
    ("<-03>3", -3 * 3600, None),
    ("EST5EDT", -5 * 3600, "EDT"),                 # US rules when none are given
    ("XXX3YYY,J60/2,300", -3 * 3600, "YYY"),
    (":CET-1CEST,M3.5.0,M10.5.0/3", 3600, "CEST"),
])
def test_the_posix_forms_parse(tz, east, summer):
    z = wc.parse(tz)
    assert z is not None and z[1] == east and z[2] == summer


def test_julian_and_zero_based_days_are_their_own_days():
    wc.set_zone("AAA0BBB,J60/0,300/0")      # J60 is March 1 every year; 300 from 0
    assert wc.offset(_at(2028, 2, 29, 12)) == 0          # leap day: not J60
    assert wc.offset(_at(2028, 3, 1, 0)) == 3600
    # day 300 of a leap year is Oct 27, its 00:00 summer time 23:00 UTC before
    assert wc.offset(_at(2028, 10, 26, 22, 59)) == 3600
    assert wc.offset(_at(2028, 10, 26, 23)) == 0


@pytest.mark.parametrize("tz", ["", "C1", "CET", "CET-1CEST,M13.1.0,M10.5.0",
                                "CET-1CEST,M3.5.0", "CET-1CEST,M3.5.0,M10.5.0junk",
                                "<+01-1", None, 7])
def test_a_string_that_is_not_a_zone_is_refused_and_utc_kept(tz):
    assert wc.parse(tz) is None
    wc.set_zone(BELGRADE)
    assert wc.set_zone(tz) is False
    assert wc.zone() == wc.UTC and wc.offset(_at(2026, 7, 1)) == 0


def test_the_picker_names_its_zones_and_their_standard_offsets():
    assert wc.place(BELGRADE) == "Paris, Berlin, Belgrade"
    assert wc.place("ABC-3") == "ABC"          # a string set some other way
    assert [wc.utc_label(e) for e in (0, 3600, -12600, 20700)] == \
        ["UTC", "UTC+1", "UTC-3:30", "UTC+5:45"]


# -- the clock's state ---------------------------------------------------------------

def _kernel(state):
    wc._kernel[:] = [lambda: state, True]


def test_unsynced_has_no_time_of_day_and_a_given_time_still_converts():
    _kernel(0)
    assert not wc.synced()
    assert wc.localtime() is None
    wc.set_zone(BELGRADE)
    assert wc.localtime(_at(2026, 1, 1))[3] == 1
    _kernel(2)                                 # asking: still unsynced
    assert wc.localtime() is None
    _kernel(1)
    assert wc.synced() and wc.localtime() is not None


def test_without_a_kernel_clock_a_machine_clock_past_2020_is_synced(monkeypatch):
    wc._kernel[:] = [None, True]
    monkeypatch.setattr(wc, "_now", lambda: _at(2019, 12, 31))
    assert not wc.synced()
    monkeypatch.setattr(wc, "_now", lambda: _at(2026, 11, 6))
    assert wc.synced()


def test_the_host_kernel_clock_is_the_machines_and_synced():
    from runtime.host import net_binding
    assert net_binding.clock_state() == wc.SYNCED
    net_binding.clock_server("time.example.org")
    net_binding.clock_server(None)
    with pytest.raises(ValueError):
        net_binding.clock_server("x" * 64)


def test_the_bar_reads_dashes_until_synced_then_local_time(tmp_path, monkeypatch):
    ws = build_ws(tmp_path)
    bar = ws.bar_layer
    _kernel(0)
    bar._clock_at = -1
    assert bar._clock_text() == "--:--"
    assert bar._today() == (0, 0, 0)
    assert ws._today_ymd() == 0
    _kernel(1)
    monkeypatch.setattr(wc, "_now", lambda: _at(2026, 11, 6, 23, 30))
    ws.set_time_zone(BELGRADE)
    assert bar._clock_text() == "00:30"            # the zone's next day
    assert bar._today() == (2026, 11, 7)
    ws.set_time_zone("UTC0")
    assert bar._clock_text() == "23:30"


# -- the setting ------------------------------------------------------------------------

def test_the_zone_is_stored_and_read_back_at_boot(tmp_path):
    ws = build_ws(tmp_path)
    assert ws.system.get("tz") is None and wc.zone() == wc.UTC
    ws.set_time_zone(BELGRADE)
    assert ws.system.get("tz") == BELGRADE
    wc.set_zone(wc.UTC)
    ws.load_system()
    assert wc.zone() == BELGRADE
    ws.set_time_zone("not a zone")
    assert ws.system.get("tz") == wc.UTC and wc.zone() == wc.UTC


def test_the_settings_row_opens_the_picker_and_a_pick_sets_the_zone(tmp_path):
    from runtime import settings_layer
    ws = build_ws(tmp_path)
    ws.open_settings()
    sl = ws.settings_layer
    i = sl.row_index("tz")
    assert sl._settings_rows()[i] == ("tz", "TIME ZONE", "tz")
    assert sl._value("tz", "tz") == "UTC"
    sl.settings_adjust(1, i)
    assert sl.has_page() and sl._page_of() == "tz"
    sl._draw_page()
    pick = [k for k, (p, _tz) in enumerate(wc.ZONES) if p == "Tokyo"][0]
    real = settings_layer._ui.link
    settings_layer._ui.link = lambda label, value=None, o=None: \
        (o or {}).get("id") == "tz%d" % pick or real(label, value, o)
    try:
        sl._draw_page()
    finally:
        settings_layer._ui.link = real
    assert not sl.has_page()
    assert wc.zone() == "JST-9" and ws.system.get("tz") == "JST-9"
    assert sl._value("tz", "tz") == "Tokyo"
    sl.open_tz()
    sl.back()
    assert not sl.has_page()


def test_the_zone_stays_on_its_console_and_the_server_on_its_board():
    from runtime import moy_sync
    assert moy_sync.key_class("tz") == moy_sync.LOCAL
    assert moy_sync.key_class("ntp_server") == moy_sync.BOARD


def test_an_app_asks_whether_the_clock_is_synced():
    from runtime import moybyte
    _kernel(0)
    assert moybyte.clock_synced() is False
    _kernel(1)
    assert moybyte.clock_synced() is True


# -- the time a certificate's dates are read against ----------------------------------

def test_tls_reads_real_time_once_synced_and_the_build_day_floor_before():
    from runtime.host import net_binding
    lib = net_binding._lib()
    floor = lib.moy_clock_build_day()
    assert floor % 86400 == 0 and floor <= time.time() < floor + 2 * 86400 + 86400 * 400
    uptime = 120                                   # a board's clock before NTP
    assert lib.moy_clock_cert_time(uptime, floor, 0) == floor
    assert lib.moy_clock_future_counts(uptime, floor, 0) == 0
    later = floor + 30 * 86400                     # a clock set past the build
    assert lib.moy_clock_cert_time(later, floor, 0) == later
    assert lib.moy_clock_future_counts(later, floor, 0) == 1
    # synced: the clock as it is, a not-yet-valid date held against a
    # certificate whatever the build day
    assert lib.moy_clock_cert_time(floor - 86400, floor, 1) == floor - 86400
    assert lib.moy_clock_future_counts(floor - 86400, floor, 1) == 1

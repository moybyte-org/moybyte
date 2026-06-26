"""HostWifi (#38 follow-up): the live-sim WiFi backend reports the desktop's real
connection so network features (#22/#8) can test over real sockets. Lenient about
the test env's actual connectivity (online or sandboxed both pass)."""

from runtime import host_app


def test_real_local_ip_returns_ipv4_or_none():
    ip = host_app._real_local_ip()
    assert ip is None or (isinstance(ip, str) and ip.count(".") == 3)


def test_host_wifi_status_shape():
    connected, ssid, ip = host_app.make_host_wifi().status()
    assert isinstance(connected, bool)
    if connected:
        assert isinstance(ssid, str) and ssid
        assert isinstance(ip, str) and ip.count(".") == 3   # looks like IPv4


def test_host_wifi_scan_nonempty_tuples():
    nets = host_app.make_host_wifi().scan()
    assert nets and all(len(n) == 3 for n in nets)


def test_host_wifi_is_a_fakewifi_subclass():
    # so the deterministic test backend (FakeWifi) and the live one share behavior
    assert issubclass(host_app.HostWifi, host_app.FakeWifi)

"""The links' provider: everything the console reaches the outside through,
constructed in one place for every console board (docs/kernel_survival_2026-10.md
section 2 item 1).

`build_desktop` (device/desktop_spine.py) calls `wire_links` once, after the
shared core wiring; the P4 desk asks `c6_updater_class` for its companion
radio's updater. What it builds, in this order, each a missing Settings row or
a notice rather than a boot failure when it cannot be built:

  ws.link / ws.net     the ESP-NOW link, INERT until a multiplayer cart arms it
  ws.updater           the OTA updater over the board's store gate
  ws.cart_net          Get Carts' network
  ws.c6_updater        the companion radio's updater, where there is one
  ws.reboot_hook       the system menu's real reset
  ws.webhost           the baked web console, constructed and not started

The WiFi service itself is `make_wifi`, which the core wiring takes.
"""

from device_wifi import autoconnect_wifi
import device_wifi


def make_wifi(store, root):
    """The console's WiFi service over the store (device/device_wifi.py)."""
    return device_wifi.make_wifi(store, root)


def c6_updater_class():
    """The companion C6's updater class, or None on a build without it."""
    try:
        from moy_c6_update import C6Updater
    except ImportError:            # a build without the C6 updater: no Settings row
        return None
    return C6Updater


def wire_links(ws, link_id, update_dir, carts_root, with_sd, c6_updater, log,
               _census):
    """Attach the links to `ws`. `update_dir` is where the board's store put
    the updater's directory, `with_sd` the store gate the web console's writes
    go through (None where there is none), `c6_updater` the companion radio's
    updater class or None, `log(tag, msg)` the boot's sink and `_census` its
    stage meter."""
    # THE RADIO LINK (#7/#65): the console's one ESP-NOW owner. Built here,
    # INERT until a cart with the "multiplayer" permission runs (ws.link_arm
    # starts the radio; pm=PM_NONE costs power and a console on its shelf has
    # nobody to talk to).
    try:
        from moy_espnow import make_link
        ws.link = make_link(board=link_id,
                            name=ws.system.get("name", link_id))
        ws.net = ws.link.net
    except Exception as exc:  # noqa: BLE001 -- no radio must never cost a console
        log("boot", "link unavailable: %s" % exc)
        ws.link = None
    _census("link")
    # OTA (#53): update_dir is where the board's store said (a copied image,
    # the pending marker), and every write -- the slot's included -- goes
    # through the console's store gate: the SD bracket where the card shares
    # the panel's bus, a plain call-through elsewhere.
    try:
        import moy_ota
        ws.updater = moy_ota.OtaUpdater(ws._with_sd, update_dir=update_dir)
    except Exception as exc:  # noqa: BLE001
        log("boot", "OTA updater unavailable: %s" % exc)
    if ws.updater is not None:
        try:
            ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))
        except Exception as exc:  # noqa: BLE001
            log("boot", "OTA wifi wiring failed: %s" % exc)
    # The network Get Carts fetches indexes and carts through (#124), over the
    # OTA's HTTP client; the app takes its own radio lease.
    try:
        from cart_net import make_cart_net
        ws.cart_net = make_cart_net(ws.wifi, autoconnect_wifi)
    except Exception as exc:  # noqa: BLE001 -- no store network is a notice in the app
        log("boot", "Get Carts network unavailable: %s" % exc)
    if c6_updater is not None and ws.updater is not None:
        # The companion radio's own updater (#7/#58): Settings -> UPGRADE C6
        # RADIO. Failure is a missing Settings row, never a boot failure.
        try:
            ws.c6_updater = c6_updater(ws.updater)
        except Exception as exc:  # noqa: BLE001
            log("boot", "C6 updater unavailable: %s" % exc)
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:  # noqa: BLE001
        log("boot", "reboot hook unavailable: %s" % exc)
    _census("ota")
    # WEB CONSOLE (moycore plan 3.4 pull half): the wasm console baked into
    # the image, served from the board. Constructed, NOT started -- __init__
    # binds no socket, so injecting it only makes the Settings row appear.
    try:
        from moy_webhost import make_webhost
        ws.webhost = make_webhost(ws, carts_root,
                                  autoconnect=autoconnect_wifi,
                                  with_sd=with_sd)
    except Exception as exc:  # noqa: BLE001
        log("boot", "web console unavailable: %s" % exc)
    _census("webhost")

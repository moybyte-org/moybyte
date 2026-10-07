"""A card as the cart store, on a board whose card has a bus of its own.

The T-Deck's card shares the panel's SPI host and so lives behind a bracket
(`moybyte_sd`); a board whose slot nothing else drives -- the Guition S3's SPI3,
the two ESP32-P4 boards' SDMMC slot 0 -- needs none of that: its card is the
store's own volume (`moy_store.card` over `moy_sd`), mounted ONCE at boot, and
the console runs on it like any other directory. This module is that body,
taking the one thing a board decides -- how to construct its card -- as a
function.

One store per boot, chosen before anything is written: the card when it
mounts, the internal flash when it does not. A card that is absent, will not
construct, has a filesystem the build cannot read, or mounts but cannot be
seeded all end the same way -- one line on the serial port saying which, and
the console boots on the internal store. Nothing here formats, erases or
writes to a card; the only writes are the store's own.
"""

SD_MOUNT = "/sd"

# The mount verdict, kept for the dev channel: the boot happens before a serial
# host attaches, so its printed line can be dropped unheard.
#   py __import__("card_store").STATUS
STATUS = "not attempted"


def mount(make_card, mount_point=SD_MOUNT, tag="SD", say=print):
    """Mount the card `make_card()` returns at `mount_point`. True when it is
    mounted; False, with the reason in STATUS and on `say`, when it is not."""
    global STATUS
    try:
        card = make_card()
    except Exception as exc:  # noqa: BLE001 -- no slot, no card, a dead bus
        STATUS = "no card interface: %r" % (exc,)
        say("%s: %s (%r) -- carts on internal flash"
            % (tag, _why(exc, "no card interface"), exc))
        return False
    try:
        _vfs_mount(card, mount_point)
    except Exception as exc:  # noqa: BLE001 -- no card, or a filesystem this build cannot read
        STATUS = "mount failed: %r" % (exc,)
        # deinit() hands the host back; a failed mount that keeps it makes every
        # later construction in this boot fail the same way.
        try:
            card.deinit()
        except Exception:  # noqa: BLE001
            pass
        say("%s: %s (%r) -- carts on internal flash" % (tag, _why(exc), exc))
        return False
    STATUS = "mounted"
    say("%s: card mounted at %s" % (tag, mount_point))
    return True


def _why(exc, otherwise="card unreadable"):
    """What a failed card means. The store's card volume mounts as it is
    built, so its FatFS errnos arrive from `make_card`: FR_NOT_READY -> EBUSY
    for a host that could not initialise a card, FR_NO_FILESYSTEM -> ENODEV
    for a filesystem this build cannot read. `moy_sd` names a card that never
    answered its bring-up (`card_init`)."""
    code = exc.args[0] if getattr(exc, "args", None) else None
    if code == 16 or (isinstance(code, str) and "card_init" in code):
        return "no card answered"
    if code == 19:
        return "card has no filesystem this build reads"
    return otherwise


def _vfs_mount(card, mount_point):
    try:
        import vfs
    except ImportError:
        import os as vfs
    vfs.mount(card, mount_point)


def carts_loader(make_card, seed_carts, card_root, flash_root, ota_dir,
                 mount_point=SD_MOUNT, tag="SD"):
    """A `build_desktop(load_carts=...)` hook: the cart store on the card when
    it mounts, on `flash_root` when it does not or cannot be seeded.

    The OTA directory stays on the internal flash either way: it holds a
    copied image and a pending marker, which belong to the device, not to a
    card that can be pulled out."""
    def _load(boot, store):
        ok = mount(make_card, mount_point, tag, say=boot.say)
        carts, root = boot.load_carts(
            store, seed_carts,
            root=card_root if ok else flash_root,
            media="SD" if ok else "flash",
            fallback_root=flash_root)
        return carts, root, ota_dir
    return _load

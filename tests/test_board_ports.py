"""Port resolution + board identity (tools/p4_autotest.find_port and friends).

Exists because of 2026-08-24: the ttyACM numbers shuffled between sessions and
a measurement run drove the T-Deck believing it was the P4 -- caught only by
the boot log growing an SD card and a trackball. The mitigation is two layers
of DATA (the [serial] usb id narrows, the board's own `_ota_build.BOARD`
settles), and these tests pin the resolution logic and the declarations.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import p4_autotest  # noqa: E402

P4 = os.path.join(ROOT, "firmware", "esp32_p4_wifi6_touch_lcd_7b")
TDECK = os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_mainline")
GUITION = os.path.join(ROOT, "firmware", "guition_jc3248w535")


# -- the declarations are data, and their SHAPE is part of the contract ------


def test_every_flashable_board_declares_a_usb_id():
    import re
    for d in (P4, TDECK, GUITION):
        usb = p4_autotest.declared_serial(d)["usb"]
        assert usb and re.match(r"^[0-9a-f]{4}:[0-9a-f]{4}$", usb), (d, usb)


def test_the_s3_twins_share_an_id_and_the_p4_does_not():
    """The AMBIGUITY IS A FACT, not a bug: both S3s enumerate as the SoC's own
    USB-Serial/JTAG, so usb id alone cannot split them -- which is exactly why
    find_port probes identities there and why verify_board() exists at all."""
    p4 = p4_autotest.declared_serial(P4)["usb"]
    td = p4_autotest.declared_serial(TDECK)["usb"]
    gu = p4_autotest.declared_serial(GUITION)["usb"]
    assert td == gu
    assert p4 != td


def test_declared_board_ids_are_the_ota_names():
    assert p4_autotest.declared_board_id(P4) == "p4"
    assert p4_autotest.declared_board_id(TDECK) == "tdeck"
    assert p4_autotest.declared_board_id(GUITION) == "guition_s3"


# -- usb_id_of walks sysfs ---------------------------------------------------


def test_usb_id_of_reads_a_fake_sysfs_tree(tmp_path):
    dev = tmp_path / "usbdev"
    dev.mkdir()
    (dev / "idVendor").write_text("1A86\n")     # case + newline are real-world
    (dev / "idProduct").write_text("55d3\n")
    iface = dev / "1-2:1.0"
    iface.mkdir()
    tty = tmp_path / "class_tty" / "ttyACM7"
    tty.mkdir(parents=True)
    (tty / "device").symlink_to(iface)
    got = p4_autotest.usb_id_of("/dev/ttyACM7", sys_tty=str(tmp_path / "class_tty"))
    assert got == "1a86:55d3"


def test_usb_id_of_answers_none_off_linux_or_for_non_usb(tmp_path):
    assert p4_autotest.usb_id_of("/dev/ttyACM9",
                                 sys_tty=str(tmp_path / "nope")) is None


# -- find_port resolution ----------------------------------------------------


def _usb_map(m):
    return lambda p: m.get(p)


def test_a_unique_usb_match_resolves_without_probing():
    got = p4_autotest.find_port(
        P4, ports=["/dev/ttyACM0", "/dev/ttyACM3"],
        usb_of=_usb_map({"/dev/ttyACM0": "303a:1001",
                         "/dev/ttyACM3": "1a86:55d3"}),
        prober=lambda p: pytest.fail("must not probe a unique match"))
    assert got == "/dev/ttyACM3"


def test_no_match_names_every_port_it_saw():
    with pytest.raises(RuntimeError) as e:
        p4_autotest.find_port(
            P4, ports=["/dev/ttyACM0"],
            usb_of=_usb_map({"/dev/ttyACM0": "303a:1001"}))
    assert "1a86:55d3" in str(e.value) and "/dev/ttyACM0" in str(e.value)


def test_twins_are_split_by_asking_each_board():
    answers = {"/dev/ttyACM1": "guition_s3", "/dev/ttyACM2": "tdeck"}
    got = p4_autotest.find_port(
        TDECK, ports=["/dev/ttyACM1", "/dev/ttyACM2"],
        usb_of=_usb_map({"/dev/ttyACM1": "303a:1001",
                         "/dev/ttyACM2": "303a:1001"}),
        prober=lambda p: answers[p])
    assert got == "/dev/ttyACM2"


def test_twins_with_no_matching_answer_refuse_with_the_picture():
    with pytest.raises(RuntimeError) as e:
        p4_autotest.find_port(
            TDECK, ports=["/dev/ttyACM1", "/dev/ttyACM2"],
            usb_of=_usb_map({"/dev/ttyACM1": "303a:1001",
                             "/dev/ttyACM2": "303a:1001"}),
            prober=lambda p: None)
    assert "tdeck" in str(e.value)


def test_a_non_attach_board_never_probes_bystanders():
    """Probing means OPENING, and opening a CH343 reboots its board -- a
    resolver must not reboot boards it is merely ruling out. Two CH343s is
    therefore a refusal, not a probe."""
    with pytest.raises(RuntimeError) as e:
        p4_autotest.find_port(
            P4, ports=["/dev/ttyACM3", "/dev/ttyACM5"],
            usb_of=_usb_map({"/dev/ttyACM3": "1a86:55d3",
                             "/dev/ttyACM5": "1a86:55d3"}),
            prober=lambda p: pytest.fail("probed a non-attach board"))
    assert "--port" in str(e.value)


def test_a_lone_twin_is_still_interrogated():
    """One 303a:1001 on the bus does NOT mean it is the board asked for --
    with the other S3 unplugged, the survivor 'uniquely' matches both names.
    Observed live 2026-08-24: the Guition resolved to the T-Deck's port."""
    with pytest.raises(RuntimeError) as e:
        p4_autotest.find_port(
            GUITION, ports=["/dev/ttyACM1"],
            usb_of=_usb_map({"/dev/ttyACM1": "303a:1001"}),
            prober=lambda p: "tdeck")
    assert "tdeck" in str(e.value) and "guition_s3" in str(e.value)


def test_a_lone_silent_twin_is_accepted_for_downstream_verify():
    """A board that does not answer might be wedged or mid-boot; find_port
    hands it over and P4Board.verify_board() is the second gate."""
    got = p4_autotest.find_port(
        GUITION, ports=["/dev/ttyACM1"],
        usb_of=_usb_map({"/dev/ttyACM1": "303a:1001"}),
        prober=lambda p: None)
    assert got == "/dev/ttyACM1"

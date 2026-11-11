"""The kernel's LEDC book (native/moy_kernel/moy_ledc.h), its C on the host:
channels by holder, timers shared by frequency through their states, the
light's SOLO pair, and the VM's end dropping machine.PWM's channels."""

import pytest

from runtime.host import ledc_book as B

try:
    book = B.lib()
except ImportError as e:                            # no C compiler
    pytest.skip(str(e), allow_module_level=True)

PID = 42


@pytest.fixture(autouse=True)
def fresh():
    book.moy_ledc_reset()
    yield
    book.moy_ledc_reset()


def _state(t):
    return book.moy_ledc_timer_state(t)


def test_channels_go_bottom_up_to_the_vm_and_top_down_to_a_handle():
    assert book.moy_ledc_chan_take(B.VM, False) == 0
    assert book.moy_ledc_chan_take(PID, True) == 7
    assert book.moy_ledc_chan_take(B.VM, False) == 1
    assert book.moy_ledc_chan_take(0, False) == -1, "0 is no holder"
    assert not book.moy_ledc_chan_claim(7, B.VM) and book.moy_ledc_chan_claim(1, B.VM)
    assert book.moy_ledc_chan_open(5, B.VM) and not book.moy_ledc_chan_open(7, B.VM)
    assert book.moy_ledc_chan_drop(7, B.VM) == -1 and book.moy_ledc_chan_holder(7) == PID
    for _ in range(5):
        assert book.moy_ledc_chan_take(PID, True) >= 0
    assert book.moy_ledc_chan_take(B.VM, False) == -1


def test_a_timer_is_set_once_and_shared_at_its_frequency():
    a = book.moy_ledc_chan_take(B.VM, False)
    b = book.moy_ledc_chan_take(PID, True)
    assert book.moy_ledc_timer_join(a, 0, B.FRESH) and _state(0) == B.SETTING
    # Nobody joins a timer while its holder configures it.
    assert not book.moy_ledc_timer_join(b, 0, 1000) and not book.moy_ledc_timer_usable(0, 1000)
    book.moy_ledc_timer_set(0, 1000, 13, 4)
    assert _state(0) == B.READY and B.cfg(0) == (1000, 13, 4)
    assert not book.moy_ledc_timer_join(b, 0, 50), "another frequency"
    assert book.moy_ledc_timer_join(b, 0, 1000)
    assert book.moy_ledc_timer_users(0, -1) == 2 and book.moy_ledc_timer_users(0, a) == 1
    # Shared, neither retunes it.
    assert not book.moy_ledc_timer_retune(0, a) and not book.moy_ledc_timer_retune(0, b)
    # Its last channel hands it back CLOSING, to deconfigure; then it is FREE.
    assert book.moy_ledc_chan_drop(a, B.VM) == -1
    assert book.moy_ledc_chan_drop(b, PID) == 0 and _state(0) == B.CLOSING
    assert not book.moy_ledc_timer_usable(0, B.FRESH)
    book.moy_ledc_timer_closed(0)
    assert _state(0) == B.FREE


def test_a_channel_alone_on_its_timer_retunes_it_and_one_that_moves_frees_it():
    a = book.moy_ledc_chan_take(PID, True)
    assert book.moy_ledc_timer_join(a, 1, B.FRESH)
    book.moy_ledc_timer_set(1, 1000, 13, 4)
    assert book.moy_ledc_timer_retune(1, a) and _state(1) == B.SETTING
    assert book.moy_ledc_timer_cfg(1, None, None, None) is False
    book.moy_ledc_timer_set(1, 50, 16, 4)
    assert B.cfg(1) == (50, 16, 4)
    # Moving to another timer leaves its old one FREE, as it was configured.
    assert book.moy_ledc_timer_join(a, 2, B.FRESH)
    assert _state(1) == B.FREE and book.moy_ledc_chan_timer(a) == 2


def test_the_lights_pair_is_reserved_and_its_timer_never_joined():
    assert book.moy_ledc_reserve(7, 3, B.LIGHT, 25000, 10, 0)
    assert book.moy_ledc_chan_holder(7) == B.LIGHT and B.cfg(3) == (25000, 10, 0)
    a = book.moy_ledc_chan_take(PID, True)
    assert a == 6
    assert not book.moy_ledc_timer_join(a, 3, 25000) and not book.moy_ledc_timer_usable(3, 25000)
    assert not book.moy_ledc_timer_retune(3, 7)
    assert not book.moy_ledc_reserve(6, 2, B.LIGHT, 25000, 10, 0), "channel taken"
    assert not book.moy_ledc_reserve(5, 3, B.LIGHT, 25000, 10, 0), "timer taken"


def test_the_vms_end_drops_its_channels_and_leaves_the_rest():
    from runtime import moy_lease as N
    v = book.moy_ledc_chan_take(B.VM, False)
    h = book.moy_ledc_chan_take(PID, True)
    w = book.moy_ledc_chan_take(B.VM, False)
    assert book.moy_ledc_timer_join(v, 0, B.FRESH)
    book.moy_ledc_timer_set(0, 1000, 13, 4)
    assert book.moy_ledc_timer_join(h, 0, 1000)
    assert book.moy_ledc_timer_join(w, 1, B.FRESH)       # left SETTING by a raise
    N.vm_stop()
    assert B.holders() == [0] * 7 + [PID]
    assert B.cfg(0) == (1000, 13, 4) and book.moy_ledc_timer_users(0, -1) == 1
    assert _state(1) == B.FREE

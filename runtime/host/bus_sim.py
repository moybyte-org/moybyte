"""The far end of the kernel's bus twins (native/moy_kernel/moy_devbus.h): what
a compiled program's uart, spi, i2c, pwm and adc handles (moy_dev.h) drive on
the host, in the host's one library (runtime/host/host_native.py) by ctypes.
Python's own lease objects have theirs in runtime/host/lease_sim.py.

`feed(unit, data)` is what a UART's peer sent, `sent(unit)` what it wrote,
`loopback(unit, on)` joins its TX to its RX; `spi_sent(unit)` is every byte
an SPI host clocked out (it reads 0xff); `i2c(addr, regs)` puts a device at
`addr`, its registers a read takes from its pointer (the first byte a write
sends) and a write fills after it, and `i2c_regs(addr)` reads them back;
`adc(pin, v)` is what an ADC reads; `pwm(pin)` is a PWM's (freq, duty), freq 0
where none drives the pin. `reset()` forgets every far end."""

import ctypes

from runtime.host import moy_lease as _N

_U8, _Z, _B, _P = ctypes.c_uint8, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p
_SIGS = (
    ("moy_devbus_sim_uart_feed", [_U8, _B, _Z], None),
    ("moy_devbus_sim_uart_sent", [_U8, _P, _Z], _Z),
    ("moy_devbus_sim_uart_loop", [_U8, ctypes.c_bool], None),
    ("moy_devbus_sim_spi_sent", [_U8, _P, _Z], _Z),
    ("moy_devbus_sim_i2c", [_U8, _B, _Z], None),
    ("moy_devbus_sim_i2c_regs", [_U8, _P, _Z], _Z),
    ("moy_devbus_sim_adc", [ctypes.c_int, ctypes.c_uint16], None),
    ("moy_devbus_sim_pwm", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint32)], ctypes.c_uint32),
    ("moy_devbus_sim_reset", [], None),
)
_CAP = 1024


def _lib():
    lib = _N._lib()
    for name, args, res in _SIGS:
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, res
    return lib


def _out(fn, *args):
    buf = ctypes.create_string_buffer(_CAP)
    n = fn(*args, buf, _CAP)
    return buf.raw[:n]


def feed(unit, data):
    data = bytes(data)
    _lib().moy_devbus_sim_uart_feed(unit, data, len(data))


def sent(unit):
    return _out(_lib().moy_devbus_sim_uart_sent, unit)


def loopback(unit, on):
    _lib().moy_devbus_sim_uart_loop(unit, bool(on))


def spi_sent(unit):
    return _out(_lib().moy_devbus_sim_spi_sent, unit)


def i2c(addr, regs):
    regs = bytes(regs)
    _lib().moy_devbus_sim_i2c(addr, regs, len(regs))


def i2c_regs(addr):
    return _out(_lib().moy_devbus_sim_i2c_regs, addr)


def adc(pin, value):
    _lib().moy_devbus_sim_adc(pin, value)


def pwm(pin):
    duty = ctypes.c_uint32()
    freq = _lib().moy_devbus_sim_pwm(pin, ctypes.byref(duty))
    return freq, duty.value


def reset():
    _lib().moy_devbus_sim_reset()

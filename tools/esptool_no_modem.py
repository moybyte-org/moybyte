"""Run esptool while avoiding combined RTS/DTR ioctls.

Some ESP32-S3 native USB serial nodes accept normal reads but reject modem
control ioctls. This wrapper leaves hardware flow control off and replaces
esptool's combined RTS/DTR update with separate pyserial calls, which work on
the T-Deck Plus USB CDC node seen during this spike.
"""

import sys

import serial
import serial.serialposix


_serial_for_url = serial.serial_for_url


def _ignore_modem_control(self):
    return None


def _patched_serial_for_url(*args, **kwargs):
    kwargs["rtscts"] = False
    kwargs["dsrdtr"] = False
    return _serial_for_url(*args, **kwargs)


serial.serialposix.Serial._update_dtr_state = _ignore_modem_control
serial.serialposix.Serial._update_rts_state = _ignore_modem_control
serial.serial_for_url = _patched_serial_for_url

import esptool  # noqa: E402
import esptool.reset  # noqa: E402


def _set_dtr_and_rts_separately(self, dtr=False, rts=False):
    self.port.setDTR(dtr)
    self.port.setRTS(rts)


esptool.reset.ResetStrategy._setDTRandRTS = _set_dtr_and_rts_separately


if __name__ == "__main__":
    sys.exit(esptool._main())

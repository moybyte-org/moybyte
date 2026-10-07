"""The host's input state: the one input table (runtime/moy_input.py) over the
host's vocabulary.

Edge-detected buttons plus the pointer, the same held / pressed / released
contract the boards' InputState has, so cartridges poll the same way on host
and device. The table is moy_input's InputTable; what is the host's is
BUTTONS, the eight names a keyboard, a mouse and the browser can press
(libmoy's seven and `home`). A name outside them is refused.
"""

try:                                    # device-shaped trees: flat
    from moy_input import HOST_NAMES, InputTable
except ImportError:                     # host: the runtime package
    from runtime.moy_input import HOST_NAMES, InputTable


class InputState(InputTable):
    BUTTONS = HOST_NAMES

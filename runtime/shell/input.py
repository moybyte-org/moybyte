"""The host's input state: the one input table (runtime/host/moy_input.py) over the
host's vocabulary.

Edge-detected buttons plus the pointer, the same held / pressed / released
contract the boards' InputState has, so cartridges poll the same way on host
and device. The table is moy_input's InputTable; what is the host's is
BUTTONS, the eight names a keyboard, a mouse and the browser can press
(libmoy's seven and `home`). A name outside them is refused.
"""

from moy_input import HostInputTable as InputState  # a VM: the native module

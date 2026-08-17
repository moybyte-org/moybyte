"""The device HOME of the cart namespace -- a re-export since 2026-08-17.

make_api lived here as the ~80%-line-identical twin of runtime/host_api.py's,
extracted from moy_runtime so both boards could stage one copy (#58) -- and the
two copies drifted anyway (this one's `_Layer` had lost `tline`; the host's
multi-tile spr had lost the #63 span cache). THE body is
`runtime/cart_api.py` now, one function object for every tier; the merge notes
live in its docstring, and tests/test_cart_api_unified.py pins the identity.

This module stays because it is the name both boards' moy_runtime imports
(`from device_api import make_api`) and the name their board.toml allowlists
stage -- an import surface is a contract, and the unification's whole point is
that no import site moved. If the device tier ever grows a genuinely
device-only cart verb, THIS is where its wrapper would live; today there is
none, and an empty home is the honest state."""

try:                                    # device: staged flat namespace
    from cart_api import (CART_BUTTONS, _Layer,  # noqa: F401 -- re-exports
                          _decode_moyimg, make_api)
except ImportError:                     # host tests importing the device module
    from runtime.cart_api import (CART_BUTTONS, _Layer,  # noqa: F401
                                  _decode_moyimg, make_api)

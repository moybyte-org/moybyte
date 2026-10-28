"""A theme's tokens as the shell draws them (docs/look_2026-10.md step 5):
`chrome.live_colors`, each RGB role in its block slot, with the block put
where the canvases and the readability check read it, as the look does."""

import moy_ui
from runtime.chrome import live_colors
from runtime.host_canvas import make_system_canvas  # noqa: F401  (installs device_canvas)
import device_canvas


def live_tokens(name, variant="dark"):
    live = live_colors(name, variant)
    device_canvas.set_block(live.block)
    moy_ui.theme_block(live.block, live.used)
    return live.tokens

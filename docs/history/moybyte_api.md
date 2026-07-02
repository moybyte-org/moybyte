# Moybyte API

> **Legacy — `.moyproj` SDK.** This documents the older `.moyproj` project API
> (`from moybyte import *`). The current v0.4 `.moy` console uses a different,
> TIC-80-style API — see **[`moy_cart_api.md`](../moy_cart_api.md)**. This SDK is still
> maintained (it seeds the block-programming ladder); it is **not** the console cart API.

Kid projects should import from the portable API:

```python
from moybyte import *
```

Core functions:

```text
run(update=None, draw=None)
@game.update
@game.draw
sprite(name, x=0, y=0, w=8, h=8)
draw_sprite(sprite)
clear(color=0)
text(value, x, y)
rect(x, y, w, h, color=1, fill=True)
circle(x, y, r, color=1, fill=False)
line(x1, y1, x2, y2, color=1)
button(name)
button_pressed(name)
button_released(name)
beep()
```

Projects and generated block code should stay portable: use `moybyte`, simple built-ins, `math`, and `random`. Avoid direct pygame, OS, networking, subprocess, or arbitrary package imports in kid project code.

Run the checker before adding examples:

```bash
moybyte check-portable examples/tiny_runner.moyproj
```

The desktop simulator supports `--fps` and `--scale` for manual runs:

```bash
moybyte run examples/tiny_runner.moyproj --fps 30 --scale 4
```

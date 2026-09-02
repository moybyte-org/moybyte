"""The cart API's NAMES -- one list, for everything that is not make_api.

`make_api` builds the cart's global namespace, twice: `runtime/host_api.py` for
the host and the wasm head, `device_api.py` for both boards. Those two are a
deliberate pair (one contract, two backends) and a test asserts their key sets
match exactly.

Everything ELSE that needs to know what a cart verb is called was copying that
list by hand, and by 2026-08-15 there were four copies drifting apart:

  * `code_layer._HL_BUILTINS` -- the code editor's pink syntax-highlight class.
    37 names against make_api's 62, one of them (`pset`) renamed to `pix` long
    ago. Cosmetic, so nothing ever noticed.
  * `blocks._RESERVED_NAMES` -- names a #48 custom block may NOT take, because
    a proc compiles to `def <name>(...)` and would SHADOW the verb. Missing
    `sspr`/`tline`/`tri`/`trib`/`view`/`players`/`net`/`on_net`, so a kid could
    name a block `tri` and silently break every triangle in their cart. NOT
    cosmetic.

Both carried a comment saying to keep them in sync with make_api by hand. Both
had stopped. So the names live HERE, the two consumers derive from them (each
adding its own local extras -- language builtins, compiler helpers), and
tests/test_moy_spec_cart.py pins this tuple against what make_api actually
builds on BOTH backends. That is the part that makes it a single source rather
than a fifth copy: nothing here is authoritative on its own, the test is.

Imports nothing, so it costs a frozen module and no runtime dependency (the
same shape as runtime/lua_ext.py, and it is staged to the boards by the same
shared denylist).

This is the CART's vocabulary, not the console's -- the capability-gated names
are included (`wifi`, `net`, `on_net`, the #85 scene/actor verbs), because a
name that MIGHT be injected must still be highlighted and must still be
reserved. A name here that a given run does not inject costs nothing in either
consumer.
"""

CART_VERBS = (
    # Canvas size (SPEC.md 6) -- constants, but cart-visible names all the same.
    "W", "H",
    # Draw + state
    "cls", "pix", "line", "rect", "rectb", "circ", "circb", "spr",
    "tri", "trib", "sspr", "tline", "print",
    "clip", "camera", "pal", "palt", "col", "background", "view",
    # Layers + images. `make_layer`/`draw_layer` are CORE since the
    # 2026-08-19 vendor (SPEC.md 6; upstream b9dbba1 moved them out of the
    # §10 `layers` extension -- a verb that degrades truthfully cannot be an
    # extension). `image` is moybyte's own, still `moybyte.images`.
    "make_layer", "draw_layer", "Image", "image",
    # Tilemap + the tile flags it filters on (SPEC.md 3.5 / 7.2)
    "map", "mget", "mset", "fget", "fset",
    # Input
    "btn", "btnp", "players", "key", "keyp", "touch", "mouse", "textmode",
    # Audio
    "sfx", "beep", "music", "music_stop", "sound_stop", "volume",
    # Misc / lifecycle
    "time", "pmem", "cfg", "rnd", "flr", "quit",
    # Desk Lab interop (#78)
    "table", "text",
    # Capability-gated: network (#38), multiplayer (#65)
    "wifi", "net", "on_net",
    # Capability-gated: physical pins (#9), on a host that has them
    "pin_write", "pin_read",
    # Capability-gated: scenes + the actor world (#85 / #109)
    "scene", "load_scene", "draw_scene",
    "actors", "touching", "move_actor", "move_actor_to", "remove_actor",
)

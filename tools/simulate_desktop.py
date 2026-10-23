#!/usr/bin/env python3
"""Run the Moybyte v0.4 fantasy workstation on the host.

Boots into the cartridge **launcher**; open any cartridge (wallpaper / game) into
the desktop shell, tweak it in "Make it mine", Run, Save, Home back to the
gallery. No device needed. Drive it live (pygame) or headlessly via a script.

  # Renders the SAME shared console as the T-Deck (320x240, petme128 font), on a
  # DESKTOP keyboard + mouse -- the same arrangement the browser build uses, not
  # an emulation of the T-Deck's trackball:
  #   MOUSE  = the cursor. Moving it HOVERS (desk icons and card grids light up),
  #            click/drag taps and drags. Nothing steers a cursor with the keys.
  #   ARROWS = the d-pad (WASD also works). Z = A, X = B, SPACE = A too.
  #            Enter = run, which is also "open this cart" in the launcher.
  #            BACKSPACE is a plain key. SUPER (Cmd, the Windows key) is the
  #            console's key: the system menu, or the pause menu over a game
  #            (Quit leaves it). Esc closes a menu, or quits the sim.
  #   The T-Deck's own keys differ ON PURPOSE (it has no arrows, and its Z/X sit
  #   under the WASD thumb): there it is WASD + L/K. Same buttons, two keyboards.
  #   The code editor is FULL-SCREEN: letters type, the bottom symbol palette
  #   taps in = ( ) [ ] { } < > etc., ARROWS move the caret (DRAG scrolls), and
  #   the top-bar play/save/X icons run / save / close.
  python tools/simulate_desktop.py

  # headless demo -> animated GIF of the whole tour
  python tools/simulate_desktop.py --demo --gif demo.gif

  # headless custom script
  python tools/simulate_desktop.py --gif out.gif --script "wait:20 right run wait:40 super"

  # launch a single cartridge directly (skip the launcher)
  python tools/simulate_desktop.py --cart system_carts/moybyte.star_catcher.moy

  # a roomy responsive desktop at a larger SYSTEM canvas (#39): the desktop reflows
  # to fill it; the game stays a fixed 320x240, composited as a centered viewport.
  python tools/simulate_desktop.py --size 960x600
  python tools/simulate_desktop.py --size 960x600 --font-scale 2

  # the Picotron-style windowed desktop (#73 -- the P4 "One" presentation):
  # launcher = the desktop, apps open as draggable windows, a playtest runs in a
  # window beside the editor.
  python tools/simulate_desktop.py --size 1024x600 --font-scale 2 --windowed
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime import host_app  # noqa: E402  (runs the SHARED console.Workstation)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEM_CARTS = os.path.join(ROOT, "system_carts")
DEFAULT_SAVE_DIR = os.path.expanduser("~/.moybyte/projects")
# Tour (shared-console buttons): open a cart, play it, EXIT, move, open another, play,
# exit. The `super` token is the console's key; over a game it opens the pause
# menu, whose rows are Resume, Restart, Options, Quit (a press needs a gap
# frame to be a second edge).
_EXIT = "super wait:4 down:2 down:2 down:2 run wait:8"
DEMO_SCRIPT = (
    "wait:18 run wait:40 " + _EXIT + " down run wait:50 " + _EXIT +
    " down down run wait:40 " + _EXIT + " wait:20"
)


def _require(module, package, what, extra, hint=""):
    """Import a lazily-needed third-party module, or exit with a readable hint.

    These deps are optional on purpose (the headless/scripted paths need neither),
    so a missing one used to surface as a bare ModuleNotFoundError traceback on the
    very first command a new developer runs. Say what to install instead.
    """
    try:
        return __import__(module)
    except ImportError:
        sys.exit("error: %s needs %s, which is not installed.\n"
                 "  install it:  %s -m pip install -e '.[%s]'   (or: make setup)%s"
                 % (what, package, sys.executable, extra, hint))


def _coerce(value):
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def parse_size(text):
    """Parse a '--size WxH' string into (w, h). The SYSTEM canvas size (#39): the
    desktop renders here, responsive; the game stays a fixed 320x240 viewport. The
    default (320x240) is exactly today (the T-Deck panel)."""
    w, _, h = text.lower().partition("x")
    return (int(w), int(h))


def parse_script(text):
    actions = []
    for tok in text.split():
        name, _, cnt = tok.partition(":")
        count = int(cnt) if cnt else 1
        if name == "wait":
            actions.extend([None] * count)
        else:
            actions.append(name)
            actions.extend([None] * (count - 1))
    return actions


def run_script(driver, actions, dt):
    """Drive the scripted tour. Returns (images, errors).

    `errors` is why this returns a pair: the tour is the ONLY lane that boots
    the console through the production import path rather than the test
    harness's meta-path finder, so it is the gate for "does the shell actually
    come up" -- and it used to print `Moybyte frame error: ...` and exit 0,
    which is a gate that cannot fail. A cart crash sets `ws.cart_error` (the
    Player's forwarding property) and the panel then clears it on recovery, so
    sample it EVERY frame and keep the distinct strings; sampling once at the
    end sees nothing.
    """
    images = []
    errors = []
    seen = set()
    ws = getattr(driver, "ws", None)
    for press in actions:
        if press == "super":
            driver.system_key()
        elif press:
            driver.press(press)
        driver.frame(dt)
        err = getattr(ws, "cart_error", None) if ws is not None else None
        if err and err not in seen:
            seen.add(err)
            errors.append(err)
        cv = driver.current_canvas() if hasattr(driver, "current_canvas") else driver.rt.canvas
        images.append((cv.w, cv.h, driver.rgb888()))
    return images, errors


def save_gif(images, path, scale):
    _require("PIL", "pillow", "--gif", "dev")
    from PIL import Image

    frames = []
    for (w, h, buf) in images:
        img = Image.frombytes("RGB", (w, h), buf)
        if scale != 1:
            img = img.resize((w * scale, h * scale), Image.NEAREST)
        frames.append(img)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=33, loop=0)
    print("wrote %s (%d frames, %dx%d)" % (path, len(frames), images[0][0] * scale, images[0][1] * scale))


def _open_named_cart(ws, cart_path, carts_dir):
    """Copy a named .moy into the store (if needed), select and open it."""
    name = os.path.basename(os.path.normpath(cart_path))
    dst = os.path.join(carts_dir, name)
    if os.path.abspath(cart_path) != os.path.abspath(dst) and not os.path.exists(dst):
        import shutil
        shutil.copytree(cart_path, dst)
    cat = host_app.moy_catalogue
    ws.launcher.items = [c for c in (cat.load(e["h"])
                                     for e in cat.catalogue(ws.carts_root)) if c]
    for i, c in enumerate(ws.launcher.items):
        if os.path.abspath(c["path"]) == os.path.abspath(dst):
            ws.launcher.sel = i
            break
    ws.open()


def run_live(driver, dt, scale):
    pygame = _require("pygame", "pygame", "the live simulator window", "sim",
                      "\n  or run it headless:  %s %s --demo"
                      % (os.path.basename(sys.executable), sys.argv[0]))

    cv = driver.current_canvas()
    w, h = cv.w, cv.h
    pygame.init()
    screen = pygame.display.set_mode((w * scale, h * scale))
    pygame.display.set_caption("Moybyte workstation")
    clock = pygame.time.Clock()
    # THE MOUSE IS THE CURSOR on this tier, like the browser's and unlike the
    # T-Deck's (whose cursor really is a trackball). An idle move hovers, a drag
    # drags; the keyboard steers nothing but the game.
    #
    # THE KEYBOARD is the browser page's: every key goes to the driver as its
    # code and the modifiers held with it (ConsoleDriver.key_down), which
    # presses what it presses beside typing it -- arrows + Z/X, PICO-8's and
    # every emulator's (owner call 2026-08-14), the letters only while a cart
    # owns the keys (runtime/host_api.py's DESK_KEYS and CART_KEYS). The
    # modifiers go as a level too, so Super acts on its release (the tap).
    from runtime import moy_input as mi
    mod_keys = {}
    for names, bit in ((("K_LCTRL", "K_RCTRL"), mi.CTRL), (("K_LSHIFT", "K_RSHIFT"), mi.SHIFT),
                       (("K_LALT", "K_RALT"), mi.ALT),
                       (("K_LSUPER", "K_RSUPER", "K_LGUI", "K_RGUI", "K_LMETA", "K_RMETA"),
                        mi.SUPER)):
        for n in names:
            if hasattr(pygame, n):
                mod_keys[getattr(pygame, n)] = bit
    named = {pygame.K_RETURN: 0x0D, pygame.K_KP_ENTER: 0x0D, pygame.K_BACKSPACE: 0x08,
             pygame.K_TAB: 0x09, pygame.K_DELETE: 0x7F, pygame.K_LEFT: mi.KEY_LEFT,
             pygame.K_RIGHT: mi.KEY_RIGHT, pygame.K_UP: mi.KEY_UP, pygame.K_DOWN: mi.KEY_DOWN,
             pygame.K_PAGEUP: mi.KEY_PGUP, pygame.K_PAGEDOWN: mi.KEY_PGDN}
    for n in range(12):
        named[getattr(pygame, "K_F%d" % (n + 1))] = mi.KEY_F1 + n
    held_mods = set()

    def mods_now():
        m = 0
        for k in held_mods:
            m |= mod_keys[k]
        return m

    def code_of(ev, mods):
        """The byte a key types with Shift applied and Ctrl not, or a KEY_*."""
        if ev.key in named:
            return named[ev.key]
        if mods & (mi.CTRL | mi.ALT | mi.SUPER) and 0x20 <= ev.key <= 0x7E:
            c = ev.key
            if mods & mi.SHIFT and 0x61 <= c <= 0x7A:
                c -= 32
            return c
        if ev.unicode and 0x20 <= ord(ev.unicode) <= 0x7E:
            return ord(ev.unicode)
        return None

    mouse_down = False
    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type in (pygame.KEYDOWN, pygame.KEYUP) and ev.key in mod_keys:
                (held_mods.add if ev.type == pygame.KEYDOWN else held_mods.discard)(ev.key)
                driver.set_mods(mods_now())
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                if driver.takes_escape() or driver.in_code_editor():
                    driver.escape()       # a menu closes, Settings or an app steps back
                elif driver.in_text_mode():
                    driver.type_char(0x1B)   # a cart taking text owns its Esc
                else:
                    running = False       # with no menu open, Esc quits the simulator
            elif ev.type == pygame.MOUSEWHEEL:
                # notches, positive right and down (SDL's y is positive up)
                driver.wheel(ev.x, -ev.y, mods_now())
            elif ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and ev.button == 2:
                driver.middle(ev.pos[0] // scale, ev.pos[1] // scale,
                              ev.type == pygame.MOUSEBUTTONDOWN)
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                mouse_down = True
                driver.set_mods(mods_now())   # Shift+click extends a selection
                driver.touch(ev.pos[0] // scale, ev.pos[1] // scale)   # tap
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                mouse_down = False
                driver.touch_up()
            elif ev.type == pygame.MOUSEMOTION:
                if mouse_down:
                    driver.touch_drag(ev.pos[0] // scale, ev.pos[1] // scale)
                else:
                    # THE MOUSE IS THE CURSOR here, exactly as in the browser
                    # (owner call): an idle move is a hover, so the desk icons
                    # and card grids light up under it.
                    driver.hover(ev.pos[0] // scale, ev.pos[1] // scale)
            elif ev.type in (pygame.KEYDOWN, pygame.KEYUP):
                mods = mods_now()
                code = code_of(ev, mods)
                if code is not None:
                    (driver.key_down if ev.type == pygame.KEYDOWN else driver.key_up)(code, mods)
        # (No trackball nudge: this tier's cursor IS the mouse. ConsoleDriver.pan
        # stays for backends that really have a trackball -- the T-Deck's own.)
        driver.frame(dt)
        surf = pygame.image.frombuffer(driver.rgb888(), (cv.w, cv.h), "RGB")
        screen.blit(pygame.transform.scale(surf, (cv.w * scale, cv.h * scale)), (0, 0))
        pygame.display.flip()
        clock.tick(int(1 / dt))
    pygame.quit()


def _force_autoplay_on_open(ws):
    """Wrap ws.open so every cart it opens runs in AUTOPLAY (attract) mode.

    Carts now default to autoplay OFF so a kid actually plays; the scripted demo
    tour, though, sends no gameplay input, so without this every game would sit
    idle. Flip the just-opened cart's `autoplay` config on (if it has one) and
    re-start it so the GIF/tour stays lively."""
    _orig_open = ws.open

    def _open(cart=None):
        _orig_open(cart)
        if ws.cart and isinstance(ws.config, dict) and "autoplay" in ws.config:
            ws.config["autoplay"] = 1
            ws._start()

    ws.open = _open


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cart", help="launch a single .moy directly (skip the launcher)")
    ap.add_argument("--gif", metavar="PATH")
    ap.add_argument("--script", default=None)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if the scripted tour hit any frame error "
                         "(this is the only lane that boots through the production "
                         "import path, so CI wants it)")
    ap.add_argument("--autoplay", dest="autoplay", action="store_true", default=None,
                    help="force games into attract/autoplay mode (default ON for --demo)")
    ap.add_argument("--no-autoplay", dest="autoplay", action="store_false",
                    help="play games yourself even in the scripted demo")
    ap.add_argument("--save-dir", default=DEFAULT_SAVE_DIR)
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--fps", type=int, default=30)
    # Two-domain seam (#39): the SYSTEM canvas size. Default 320x240 = today (the
    # T-Deck panel); a larger size opens a roomy responsive desktop with the game
    # composited as a centered viewport.
    ap.add_argument("--size", default="320x240", metavar="WxH",
                    help="system canvas size (default 320x240 = the T-Deck panel)")
    ap.add_argument("--font-scale", type=int, default=1, choices=(1, 2, 3),
                    help="initial system-UI font scale 1/2/3 (system.json overrides)")
    # The Picotron-style windowed WM (#73, the P4 "One" presentation tier): the
    # launcher is the desktop and every app opens as a draggable window. Needs a
    # big --size (ignored at 320x240).
    ap.add_argument("--windowed", action="store_true",
                    help="windowed desktop WM (Picotron-style; needs a big --size)")
    args = ap.parse_args()

    dt = 1.0 / args.fps
    sys_size = parse_size(args.size)
    ws = host_app.build_workstation(args.save_dir, sys_size=sys_size,
                                    font_scale=args.font_scale,
                                    windowed=args.windowed)
    # Live windowed run -> the mix to the speakers (#16). Headless / scripted
    # runs pull it and discard it, so they stay deterministic + device-free.
    if not args.demo and args.script is None:
        ws.audio_out = host_app.SdlPump()
        # Live run -> report the desktop's REAL WiFi connection/IP (your PC is online),
        # so network features test against real Python sockets. Headless keeps FakeWifi.
        ws.wifi = host_app.make_host_wifi(host_app.moy_carts, ws.carts_root)
        # Get Carts fetches from the live indexes, or from the ones an
        # `indexes.json` beside the carts folder names (a LAN mirror).
        ws.cart_net = host_app.make_host_cart_net()
    # The scripted demo tour drives no gameplay input, so default it to autoplay
    # (so the GIF is lively); a live, interactive session defaults to PLAY.
    autoplay = args.autoplay if args.autoplay is not None else args.demo
    if autoplay:
        _force_autoplay_on_open(ws)
    if args.cart:
        _open_named_cart(ws, args.cart, args.save_dir)
    driver = host_app.ConsoleDriver(ws)

    script = DEMO_SCRIPT if args.demo else args.script
    if script is not None:
        images, errors = run_script(driver, parse_script(script), dt)
        if args.gif:
            save_gif(images, args.gif, args.scale)
        else:
            print("ran %d scripted frames" % len(images))
        for err in errors:
            print("  frame error: %s" % err)
        if errors:
            print("%d distinct frame error(s) during the tour" % len(errors))
            if args.strict:
                return 1
        return 0

    ws.arm_splash()          # boot logo: show the moybyte mascot before the launcher
    run_live(driver, dt, args.scale)


if __name__ == "__main__":
    sys.exit(main() or 0)

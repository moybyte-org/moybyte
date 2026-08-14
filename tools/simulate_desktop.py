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
  #            BACKSPACE = home, the EXIT key: HOLD ~700ms to leave a running
  #            cart (a plain key while playing otherwise). Esc quits the sim.
  #   The T-Deck's own keys differ ON PURPOSE (it has no arrows, and its Z/X sit
  #   under the WASD thumb): there it is WASD + L/K. Same buttons, two keyboards.
  #   The code editor is FULL-SCREEN: letters type, the bottom symbol palette
  #   taps in = ( ) [ ] { } < > etc., ARROWS move the caret (DRAG scrolls), and
  #   the top-bar play/save/X icons run / save / close.
  python tools/simulate_desktop.py

  # headless demo -> animated GIF of the whole tour
  python tools/simulate_desktop.py --demo --gif demo.gif

  # headless custom script
  python tools/simulate_desktop.py --gif out.gif --script "wait:20 right run wait:40 home"

  # launch a single cartridge directly (skip the launcher)
  python tools/simulate_desktop.py --cart system_carts/star_catcher.moy

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
# exit. Stage 5 retired pause -- a running cart is exited by the fw-independent triple-
# tap "home" (three consecutive home tokens = three BACKSPACE edges within the window).
DEMO_SCRIPT = (
    "wait:18 run wait:40 home home home wait:8 down run wait:50 home home home wait:8 "
    "down down run wait:40 home home home wait:20"
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
    images = []
    for press in actions:
        if press:
            driver.press(press)
        driver.frame(dt)
        cv = driver.current_canvas() if hasattr(driver, "current_canvas") else driver.rt.canvas
        images.append((cv.w, cv.h, driver.rgb888()))
    return images


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
    ws.launcher.items = host_app.moy_carts.scan(ws.carts_root)
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
    # ON A HOST WITH ARROW KEYS THE SCHEME IS ARROWS + Z/X (owner call
    # 2026-08-14), which is PICO-8's and every emulator's. It is deliberately
    # NOT the T-Deck's WASD + L/K: that board has no arrow keys at all, and Z/X
    # sit on its bottom row under the same thumb WASD needs. Same console, two
    # keyboards, two ergonomics -- the shared part is what the BUTTONS are.
    #
    # The browser page has done this since 2026-07-31 (page_core.html's AN/SC);
    # this file did not, and steered the trackball with the arrows instead. That
    # is the split being closed here: two hosts, one scheme.
    arrow_keys = {pygame.K_LEFT: "left", pygame.K_RIGHT: "right",
                  pygame.K_UP: "up", pygame.K_DOWN: "down"}
    # WASD steers too, so a kid who learned on the T-Deck is not lost here.
    # Aliasing a DIRECTION is cheap -- unlike a letter that also jumps, it
    # steals nothing from a typing cart, and in the editor the arrows and the
    # letters part ways (see the branch below).
    nav_keys = dict(arrow_keys)
    nav_keys.update({pygame.K_a: "left", pygame.K_d: "right",
                     pygame.K_w: "up", pygame.K_s: "down"})
    # BACKSPACE = the device's one console key, now the Stage-5 EXIT key. It is mapped
    # as a HELD "home" button (below, like the WASD nav keys) -- NOT a one-shot press --
    # so a sustained hold reaches the Player's hold-to-exit (~700ms) for a game and a quick
    # tap is a single edge the cart reads. In text mode the branch above routes it as typed
    # 0x08 instead (DELETE for a tool). K_h is a host-only convenience alias for held "home".
    exit_keys = (pygame.K_BACKSPACE, pygame.K_h)
    shortcuts = {pygame.K_RETURN: "run", pygame.K_z: "a",
                 pygame.K_SPACE: "a", pygame.K_x: "b"}
    mouse_down = False
    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                mouse_down = True
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
                    # and card grids light up under it. Nothing steers a virtual
                    # cursor with the keyboard on this tier any more.
                    driver.hover(ev.pos[0] // scale, ev.pos[1] // scale)
            elif ev.type == pygame.KEYDOWN and ev.key in arrow_keys \
                    and driver.in_code_editor():
                # ARROWS IN THE EDITOR still move the caret: they arrive as held
                # direction buttons now, and ConsoleDriver.frame translates those
                # back into a nav step while the code editor is open (host_api's
                # "ARROWS ON A MOUSE TIER" block). ARROWS ONLY -- W/A/S/D must
                # fall through to the typing branch below, or you cannot write
                # `draw` in the editor without the caret running away.
                driver.hold(arrow_keys[ev.key], True)
            elif ev.type == pygame.KEYDOWN and driver.in_code_editor():
                # Code editor: letters type; Esc closes.
                if ev.key == pygame.K_ESCAPE:
                    driver.escape()
                elif ev.key == pygame.K_RETURN:
                    driver.type_char(0x0D)
                elif ev.key == pygame.K_BACKSPACE:
                    driver.type_char(0x08)
                elif ev.key == pygame.K_TAB:
                    driver.type_char(0x09)
                elif ev.unicode and 0x20 <= ord(ev.unicode) <= 0x7E:
                    driver.type_char(ord(ev.unicode))
            elif ev.type == pygame.KEYDOWN and driver.in_text_mode():
                # A running cart that asked for text input via textmode(True) (#38/#42):
                # route typed unicode + Enter/Backspace/Esc to the cart's key() exactly
                # as the code editor does. The cart owns the meaning of those keys (e.g.
                # the wifi cart: Enter=connect, Backspace=delete, Esc=back to the list),
                # so Esc does NOT quit the simulator here -- the cart handles it.
                if ev.key == pygame.K_RETURN:
                    driver.type_char(0x0D)
                elif ev.key == pygame.K_BACKSPACE:
                    driver.type_char(0x08)
                elif ev.key == pygame.K_ESCAPE:
                    driver.type_char(0x1B)
                elif ev.unicode and 0x20 <= ord(ev.unicode) <= 0x7E:
                    driver.type_char(ord(ev.unicode))
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key in exit_keys:
                    driver.hold("home", True)        # held so a sustained hold = the exit gesture
                elif ev.key in shortcuts:
                    driver.press(shortcuts[ev.key])
                elif ev.key in nav_keys:
                    driver.hold(nav_keys[ev.key], True)
            elif ev.type == pygame.KEYUP and ev.key in nav_keys:
                driver.hold(nav_keys[ev.key], False)
            elif ev.type == pygame.KEYUP and ev.key in exit_keys:
                driver.hold("home", False)
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

    def _open():
        _orig_open()
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
    # Live windowed run -> stream real audio to the speakers (#16). Headless /
    # scripted runs keep the silent FakeAudio so they stay deterministic + device-free.
    if not args.demo and args.script is None:
        ws.make_audio = host_app.make_sdl_audio
        # Live run -> report the desktop's REAL WiFi connection/IP (your PC is online),
        # so network features test against real Python sockets. Headless keeps FakeWifi.
        ws.wifi = host_app.make_host_wifi(host_app.moy_carts, ws.carts_root)
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
        images = run_script(driver, parse_script(script), dt)
        if args.gif:
            save_gif(images, args.gif, args.scale)
        else:
            print("ran %d scripted frames" % len(images))
        return

    ws.arm_splash()          # boot logo: show the moybyte mascot before the launcher
    run_live(driver, dt, args.scale)


if __name__ == "__main__":
    main()

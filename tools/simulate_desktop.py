#!/usr/bin/env python3
"""Run the KidCode v0.4 fantasy workstation on the host.

Boots into the cartridge **launcher**; open any cartridge (wallpaper / game) into
the desktop shell, tweak it in "Make it mine", Run, Save, Home back to the
gallery. No device needed. Drive it live (pygame) or headlessly via a script.

  # Renders the SAME shared console as the T-Deck (320x240, petme128 font), with
  # the device's two input devices emulated:
  #   ARROWS = trackball  -> move the cursor; at the code edges the screen follows.
  #   MOUSE  = touchscreen -> tap/drag to place the pointer + activate.
  #   WASD   = keyboard buttons (launcher nav + gameplay).  Enter=run, Z=select,
  #            X=menu, H=home, Esc=quit.  The code editor is FULL-SCREEN: letters
  #            type, the bottom symbol palette taps in = ( ) [ ] { } < > etc., ARROWS
  #            move the caret (DRAG scrolls), and the top-bar play/save/X icons
  #            run / save / close.
  python tools/simulate_desktop.py

  # headless demo -> animated GIF of the whole tour
  python tools/simulate_desktop.py --demo --gif demo.gif

  # headless custom script
  python tools/simulate_desktop.py --gif out.gif --script "wait:20 right run wait:40 home"

  # launch a single cartridge directly (skip the launcher)
  python tools/simulate_desktop.py --cart system_carts/star_catcher.kcart
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime import host_app  # noqa: E402  (runs the SHARED console.Workstation)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEM_CARTS = os.path.join(ROOT, "system_carts")
DEFAULT_SAVE_DIR = os.path.expanduser("~/.kidcode/projects")
# Tour (shared-console buttons): open a cart, Home, move, open another, edit code.
DEMO_SCRIPT = (
    "wait:18 run wait:40 home wait:8 down run wait:50 home wait:8 "
    "down down run wait:40 home wait:10 run wait:10 b wait:20 a wait:40"
)


def _coerce(value):
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


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
    """Copy a named .kcart into the store (if needed), select and open it."""
    name = os.path.basename(os.path.normpath(cart_path))
    dst = os.path.join(carts_dir, name)
    if os.path.abspath(cart_path) != os.path.abspath(dst) and not os.path.exists(dst):
        import shutil
        shutil.copytree(cart_path, dst)
    ws.launcher.items = host_app.kid_carts.scan(ws.carts_root)
    for i, c in enumerate(ws.launcher.items):
        if os.path.abspath(c["path"]) == os.path.abspath(dst):
            ws.launcher.sel = i
            break
    ws.open()


def run_live(driver, dt, scale):
    import pygame

    cv = driver.current_canvas()
    w, h = cv.w, cv.h
    pygame.init()
    screen = pygame.display.set_mode((w * scale, h * scale))
    pygame.display.set_caption("KidCode workstation")
    clock = pygame.time.Clock()
    # Mirror the device's two input devices:
    #   arrows = the TRACKBALL  -> move a (visible) cursor; at the code edges the
    #            screen follows it (scrolls).
    #   mouse  = the TOUCHSCREEN -> tap/drag places the pointer absolutely.
    #   WASD   = the keyboard buttons (launcher nav + gameplay), like the T-Deck kb.
    pan_keys = {pygame.K_LEFT: (-1, 0), pygame.K_RIGHT: (1, 0),
                pygame.K_UP: (0, -1), pygame.K_DOWN: (0, 1)}
    nav_keys = {pygame.K_a: "left", pygame.K_d: "right",
                pygame.K_w: "up", pygame.K_s: "down"}
    shortcuts = {pygame.K_RETURN: "run", pygame.K_z: "a", pygame.K_x: "b",
                 pygame.K_h: "home"}
    pan_held = set()
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
            elif ev.type == pygame.MOUSEMOTION and mouse_down:
                driver.touch_drag(ev.pos[0] // scale, ev.pos[1] // scale)
            elif ev.type == pygame.KEYDOWN and ev.key in pan_keys:
                pan_held.add(ev.key)                                   # trackball
            elif ev.type == pygame.KEYUP and ev.key in pan_keys:
                pan_held.discard(ev.key)
            elif ev.type == pygame.KEYDOWN and driver.in_code_editor():
                # Code editor: letters type; arrows still pan; Esc closes.
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
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key in shortcuts:
                    driver.press(shortcuts[ev.key])
                elif ev.key in nav_keys:
                    driver.hold(nav_keys[ev.key], True)
            elif ev.type == pygame.KEYUP and ev.key in nav_keys:
                driver.hold(nav_keys[ev.key], False)
        # held arrows -> a per-frame trackball nudge
        dx = (1 if pygame.K_RIGHT in pan_held else 0) - (1 if pygame.K_LEFT in pan_held else 0)
        dy = (1 if pygame.K_DOWN in pan_held else 0) - (1 if pygame.K_UP in pan_held else 0)
        driver.pan(dx, dy)
        driver.frame(dt)
        surf = pygame.image.frombuffer(driver.rgb888(), (cv.w, cv.h), "RGB")
        screen.blit(pygame.transform.scale(surf, (cv.w * scale, cv.h * scale)), (0, 0))
        pygame.display.flip()
        clock.tick(int(1 / dt))
    pygame.quit()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cart", help="launch a single .kcart directly (skip the launcher)")
    ap.add_argument("--gif", metavar="PATH")
    ap.add_argument("--script", default=None)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--save-dir", default=DEFAULT_SAVE_DIR)
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()

    dt = 1.0 / args.fps
    ws = host_app.build_workstation(args.save_dir)
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

    run_live(driver, dt, args.scale)


if __name__ == "__main__":
    main()

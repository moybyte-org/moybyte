#!/usr/bin/env python3
"""Run the KidCode v0.4 fantasy workstation on the host.

Boots into the cartridge **launcher**; open any cartridge (wallpaper / game) into
the desktop shell, tweak it in "Make it mine", Run, Save, Home back to the
gallery. No device needed. Drive it live (pygame) or headlessly via a script.

  # live: arrows move, RUN=Enter, MENU=M, SAVE=S, HOME=H/Backspace, quit=Esc
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

from runtime import Cartridge, DesktopShell, Workstation  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEM_CARTS = os.path.join(ROOT, "system_carts")
DEFAULT_SAVE_DIR = os.path.expanduser("~/.kidcode/projects")
# Tour: open each cart, then a Make-it-mine edit on the last one.
DEMO_SCRIPT = (
    "wait:18 run wait:40 home wait:8 right run wait:50 home wait:8 "
    "right run wait:40 home wait:8 right run wait:20 menu right:8 down down right run wait:40"
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


def run_live(driver, dt, scale):
    import pygame

    cv = driver.current_canvas() if hasattr(driver, "current_canvas") else driver.rt.canvas
    w, h = cv.w, cv.h
    pygame.init()
    screen = pygame.display.set_mode((w * scale, h * scale))
    pygame.display.set_caption("KidCode workstation")
    clock = pygame.time.Clock()
    discrete = {
        pygame.K_RETURN: "run", pygame.K_m: "menu", pygame.K_TAB: "menu",
        pygame.K_s: "save", pygame.K_h: "home", pygame.K_BACKSPACE: "home",
        pygame.K_c: "code",
    }
    arrows = {pygame.K_LEFT: "left", pygame.K_RIGHT: "right", pygame.K_UP: "up", pygame.K_DOWN: "down"}
    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key in discrete:
                    driver.press(discrete[ev.key])
                elif ev.key in arrows:
                    driver.press(arrows[ev.key])             # menu/launcher nav
                    if hasattr(driver, "hold"):
                        driver.hold(arrows[ev.key], True)    # gameplay movement
            elif ev.type == pygame.KEYUP and ev.key in arrows and hasattr(driver, "hold"):
                driver.hold(arrows[ev.key], False)
        driver.frame(dt)
        cv = driver.current_canvas() if hasattr(driver, "current_canvas") else driver.rt.canvas
        surf = pygame.image.frombuffer(driver.rgb888(), (cv.w, cv.h), "RGB")
        scaled = pygame.transform.scale(surf, (cv.w * scale, cv.h * scale))
        screen.blit(scaled, (0, 0))
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
    if args.cart:
        driver = DesktopShell(Cartridge.load(args.cart), save_dir=args.save_dir)
    else:
        driver = Workstation([SYSTEM_CARTS, args.save_dir], save_dir=args.save_dir)

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

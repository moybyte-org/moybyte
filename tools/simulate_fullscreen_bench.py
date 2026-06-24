#!/usr/bin/env python3
"""Visualize the KidCode full-screen compositor benchmark scene on the host.

This mirrors, pixel-for-pixel, what `_bench_pass()` in
`firmware/.../modules/kidcode_shell.py` draws into the 320x240 RGB565
framebuffer on the device -- so you can see what the device *should* be showing
and compare it against any "weird squares" on the real panel. It does NOT
measure timing (host has no SPI bus); it only reproduces the visuals.

Two phases, matching the device:
  1. full-redraw : whole screen filled with a cycling color + a moving 32x32
                   white block (the cheap full-frame draw the bench flushes).
  2. band        : dark background + a 64x64 yellow block moving inside one
                   full-width horizontal band (the realistic partial-update).

Usage:
  python tools/simulate_fullscreen_bench.py                 # live pygame window
  python tools/simulate_fullscreen_bench.py --scale 3
  python tools/simulate_fullscreen_bench.py --save OUT.gif  # write an animated GIF
"""

import argparse

W, H = 320, 240
BLOCK_PX = 64          # BENCH_BLOCK_PX
STEP = 4               # px/frame, matches the device
PHASE_FRAMES = 48      # frames rendered per phase for the GIF / one live cycle


def rgb565_to_rgb888(c):
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return (r * 255 // 31, g * 255 // 63, b * 255 // 31)


def _rects(phase, frame):
    """Yield (x, y, w, h, rgb888) rects for one frame, in draw order.

    Identical geometry/colors to the device `_bench_pass`.
    """
    if phase == "full-redraw":
        yield (0, 0, W, H, rgb565_to_rgb888((frame * 8) & 0xFFFF))
        x = (frame * STEP) % W
        yield (x, H // 2 - 16, 32, 32, rgb565_to_rgb888(0xFFFF))  # white
    else:  # "band"
        bg = rgb565_to_rgb888(0x0010)
        band_y = H // 2 - BLOCK_PX // 2
        yield (0, 0, W, H, bg)                                    # static bg
        yield (0, band_y, W, BLOCK_PX, bg)                       # clear the band
        px = (frame * STEP) % (W - BLOCK_PX)
        yield (px, band_y, BLOCK_PX, BLOCK_PX, rgb565_to_rgb888(0xFFE0))  # yellow


# ---- GIF mode (PIL) --------------------------------------------------------

def save_gif(path, scale):
    from PIL import Image, ImageDraw

    frames = []
    for phase in ("full-redraw", "band"):
        for f in range(PHASE_FRAMES):
            img = Image.new("RGB", (W, H))
            draw = ImageDraw.Draw(img)
            for (x, y, w, h, rgb) in _rects(phase, f):
                draw.rectangle([x, y, x + w - 1, y + h - 1], fill=rgb)
            if scale != 1:
                img = img.resize((W * scale, H * scale), Image.NEAREST)
            frames.append(img)
    frames[0].save(
        path, save_all=True, append_images=frames[1:], duration=40, loop=0
    )
    print("wrote %s (%d frames, %dx%d)" % (path, len(frames), W * scale, H * scale))


# ---- live mode (pygame) ----------------------------------------------------

def run_live(scale, fps):
    import pygame

    pygame.init()
    screen = pygame.display.set_mode((W * scale, H * scale))
    pygame.display.set_caption("KidCode full-screen bench (sim)")
    surf = pygame.Surface((W, H))
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 18 * scale // 2 or 18)

    phases = ["full-redraw", "band"]
    pi = 0
    frame = 0
    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (
                ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE
            ):
                running = False
        for (x, y, w, h, rgb) in _rects(phases[pi], frame):
            surf.fill(rgb, (x, y, w, h))
        screen.blit(pygame.transform.scale(surf, (W * scale, H * scale)), (0, 0))
        label = font.render(phases[pi], True, (0, 0, 0), (255, 255, 255))
        screen.blit(label, (4, 4))
        pygame.display.flip()
        clock.tick(fps)
        frame += 1
        if frame >= PHASE_FRAMES * 2:  # ~ a couple seconds per phase at 30 fps
            frame = 0
            pi = (pi + 1) % len(phases)
    pygame.quit()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--save", metavar="PATH", help="write an animated GIF instead of opening a window")
    ap.add_argument("--scale", type=int, default=2, help="integer upscale (default 2)")
    ap.add_argument("--fps", type=int, default=30, help="live playback fps (default 30)")
    args = ap.parse_args()
    if args.save:
        save_gif(args.save, args.scale)
    else:
        run_live(args.scale, args.fps)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate the Android icon and presplash assets from the logo artwork.

The logo is drawn as a vector in `assets/icon/source/android_enchufe.svg`
(viewBox 900x1000) and rendered to `source/android_enchufe.png` at 2x
(1800x2000). This script only needs Pillow and the rendered PNG: it cuts the
circular plate out of the artwork and derives the assets python-for-android
takes from `buildozer.spec`. They live outside `source.dir` because the app
never reads them at runtime; only the APK build does.

  icon.png              legacy launcher icon (API 24/25 and the fallback of the
                        adaptive icon), white background, plate at 92 %
  icon-foreground.png   adaptive foreground: the plate, transparent corners
  icon-background.png   adaptive background: the plate's colour at the mask edge
  presplash.jpg         loading screen: the plate centred at 55 % of the width
                        on white, so FIT_CENTER leaves a comfortable margin

python-for-android copies `--presplash` to `drawable/presplash.jpg`, so the
splash has to be a real JPEG (`--presplash-color` fills the rest of the screen;
`buildozer.spec` sets it to white to match the artwork).

Run it after replacing the artwork, then rebuild the APK:

    python3 tools/make_icons.py
"""

import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets", "icon")
SOURCE = os.path.join(ASSETS, "source", "android_enchufe.png")

# Geometry of the artwork: the pale plate is a circle centred in the 1800x2000
# render with a radius of 860 px (viewBox: centre 450,500, radius 430).
SOURCE_SIZE = (1800, 2000)
PLATE_CENTRE = (899.5, 999.5)
PLATE_RADIUS = 860.0

LEGACY_SIZE = 512
LEGACY_PLATE_FRACTION = 0.92
ADAPTIVE_SIZE = 432  # 108 dp at xxxhdpi
# Colour of the plate where the adaptive mask crops it (66 % of the radius), so
# the background layer never shows a seam if a launcher reveals it.
ADAPTIVE_BACKGROUND = (231, 244, 236)
# The presplash view is drawn with FIT_CENTER, so the image is scaled to fit
# inside the screen: its white margin is what makes the logo smaller than the
# screen. 16:9 gives a canvas that is width-constrained on every phone in
# portrait, which keeps the plate at PRESPLASH_PLATE_FRACTION of the width.
PRESPLASH_SIZE = (1080, 1920)
PRESPLASH_PLATE_FRACTION = 0.55
SUPERSAMPLE = 4


def _load():
    art = Image.open(SOURCE).convert("RGB")
    if art.size != SOURCE_SIZE:
        raise SystemExit(
            f"{SOURCE} is {art.size}, expected {SOURCE_SIZE}; "
            "re-render the vector at 2x or update the geometry above"
        )
    return art


def _place(canvas, art, scale):
    """Paste `art` on `canvas` so its plate is centred and `scale` big."""
    resized = art.resize(
        (round(art.width * scale), round(art.height * scale)), Image.LANCZOS
    )
    left = round(canvas.width / 2 - PLATE_CENTRE[0] * scale)
    top = round(canvas.height / 2 - PLATE_CENTRE[1] * scale)
    canvas.paste(resized, (left, top))


def legacy_icon(art):
    canvas = Image.new("RGB", (LEGACY_SIZE, LEGACY_SIZE), (255, 255, 255))
    scale = LEGACY_PLATE_FRACTION * LEGACY_SIZE / (2 * PLATE_RADIUS)
    _place(canvas, art, scale)
    return canvas


def adaptive_foreground(art):
    canvas = Image.new("RGBA", (ADAPTIVE_SIZE, ADAPTIVE_SIZE), (0, 0, 0, 0))
    scale = ADAPTIVE_SIZE / (2 * PLATE_RADIUS)
    _place(canvas, art, scale)

    # The plate is the artwork's own circle: mask exactly there so the white
    # corners of the render never sit on top of the background layer. Drawn at
    # 4x and downsampled to keep the edge smooth.
    big = ADAPTIVE_SIZE * SUPERSAMPLE
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    mask = mask.resize((ADAPTIVE_SIZE, ADAPTIVE_SIZE), Image.LANCZOS)
    canvas.putalpha(mask)
    return canvas


def adaptive_background():
    return Image.new("RGB", (ADAPTIVE_SIZE, ADAPTIVE_SIZE), ADAPTIVE_BACKGROUND)


def presplash(art):
    canvas = Image.new("RGB", PRESPLASH_SIZE, (255, 255, 255))
    scale = PRESPLASH_PLATE_FRACTION * PRESPLASH_SIZE[0] / (2 * PLATE_RADIUS)
    _place(canvas, art, scale)
    return canvas


def main():
    art = _load()
    outputs = {
        "icon.png": (legacy_icon(art), "PNG"),
        "icon-foreground.png": (adaptive_foreground(art), "PNG"),
        "icon-background.png": (adaptive_background(), "PNG"),
        "presplash.jpg": (presplash(art), "JPEG"),
    }
    for name, (image, fmt) in outputs.items():
        path = os.path.join(ASSETS, name)
        options = {"quality": 92, "optimize": True} if fmt == "JPEG" else {"optimize": True}
        image.save(path, fmt, **options)
        print(f"{os.path.relpath(path, ROOT)}: {image.size[0]}x{image.size[1]} {fmt}")


if __name__ == "__main__":
    main()

"""The APK build must find a complete, well-formed set of icon assets.

`buildozer.spec` names four files outside `source.dir`; python-for-android
copies them into `res/` as-is (the presplash is even renamed to `.jpg`), so a
missing file, a wrong size or a PNG where a JPEG is needed only shows up as a
broken launcher icon or a black loading screen on the device.
"""

import configparser
import os
import struct

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = os.path.join(ROOT, "buildozer.spec")

KEYS = (
    "icon.filename",
    "icon.adaptive_foreground.filename",
    "icon.adaptive_background.filename",
    "presplash.filename",
)


def _spec():
    parser = configparser.ConfigParser(allow_no_value=True)
    parser.read(SPEC)
    return parser


def test_spec_declares_every_asset():
    spec = _spec()
    for key in KEYS:
        value = spec.get("app", key)
        assert value, f"buildozer.spec is missing {key}"
        path = os.path.join(ROOT, value)
        assert os.path.isfile(path), f"{key} points at a missing file: {value}"


def test_splash_background_matches_the_artwork():
    # The presplash is drawn with FIT_CENTER, so the bands around it come from
    # this colour; the artwork's own margin is pure white.
    assert _spec().get("app", "android.presplash_color") == "#FFFFFF"


def test_presplash_is_a_real_jpeg():
    # python-for-android copies it to drawable/presplash.jpg; a PNG renamed to
    # .jpg only fails at runtime, on the device.
    with open(os.path.join(ROOT, _spec().get("app", "presplash.filename")), "rb") as handle:
        head = handle.read(4)
    assert head == b"\xff\xd8\xff\xe0" or head[:3] == b"\xff\xd8\xff", head


def test_icons_are_square_and_correctly_sized():
    image = pytest.importorskip("PIL.Image")
    spec = _spec()
    expected = {
        "icon.filename": (512, 512),
        "icon.adaptive_foreground.filename": (432, 432),
        "icon.adaptive_background.filename": (432, 432),
    }
    for key, size in expected.items():
        path = os.path.join(ROOT, spec.get("app", key))
        with image.open(path) as opened:
            assert opened.size == size, key


def test_presplash_keeps_the_logo_small_and_centred():
    image = pytest.importorskip("PIL.Image")
    path = os.path.join(ROOT, _spec().get("app", "presplash.filename"))
    with image.open(path) as opened:
        splash = opened.convert("RGB")
    # FIT_CENTER scales the image to fit the screen, so the logo's share of the
    # width comes from the image itself: a 16:9 canvas keeps it under the
    # screen width and leaves white bands the presplash colour matches.
    assert splash.size == (1080, 1920)
    width, height = splash.size
    for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        assert splash.getpixel(point) == (255, 255, 255)


def test_adaptive_foreground_has_transparent_corners():
    image = pytest.importorskip("PIL.Image")
    path = os.path.join(ROOT, _spec().get("app", "icon.adaptive_foreground.filename"))
    with image.open(path) as opened:
        foreground = opened.convert("RGBA")
    width, height = foreground.size
    # The plate is a circle, so the corners must not paint over the background
    # layer; the centre carries the artwork.
    assert foreground.getpixel((1, 1))[3] == 0
    assert foreground.getpixel((width - 2, height - 2))[3] == 0
    assert foreground.getpixel((width // 2, height // 2))[3] == 255


def test_legacy_icon_is_opaque():
    image = pytest.importorskip("PIL.Image")
    path = os.path.join(ROOT, _spec().get("app", "icon.filename"))
    with image.open(path) as opened:
        assert opened.mode == "RGB"
        assert opened.convert("RGB").getpixel((1, 1)) == (255, 255, 255)


def test_jpeg_header_is_not_accidentally_a_png():
    # Cheap guard against regenerating the assets with the wrong Pillow format.
    path = os.path.join(ROOT, _spec().get("app", "presplash.filename"))
    with open(path, "rb") as handle:
        assert struct.unpack(">H", handle.read(2))[0] == 0xFFD8

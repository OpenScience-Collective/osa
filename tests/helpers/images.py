"""Generate a small PNG carrying readable digits, with no image dependency.

Nothing in this repository draws images at runtime, so there is no matplotlib
or Pillow to lean on, and pulling either in as a test dependency to draw three
digits would be a poor trade. The glyphs below are a 5x7 bitmap font scaled up;
the only property the tests need is that a vision model can read the number
back, which the live test in tests/test_integration asserts for real.

The encoder writes 8-bit grayscale (PNG color type 0), one byte per pixel,
with filter type 0 on every row: the smallest thing the format allows that is
still a valid PNG rather than a fixture that happens to work.
"""

import struct
import zlib

# 5x7 glyphs, one string per row, "1" meaning ink.
_GLYPHS: dict[str, tuple[str, ...]] = {
    "0": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
}

_GLYPH_WIDTH = 5
_GLYPH_HEIGHT = 7
_GLYPH_GAP = 1  # in font cells, not pixels

INK = 0x00
PAPER = 0xFF


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _encode_grayscale_png(pixels: bytearray, width: int, height: int) -> bytes:
    raw = b"".join(
        b"\x00" + bytes(pixels[row * width : (row + 1) * width]) for row in range(height)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def digits_png(text: str, scale: int = 14, margin: int = 24) -> bytes:
    """Render `text` (digits only) as a black-on-white PNG.

    Args:
        text: the digits to draw; every character must have a glyph above.
        scale: pixels per font cell. 14 puts a digit at 70x98 px, large
            enough that no downscaling on the way to a model loses it.
        margin: white border in pixels, so the digits never touch an edge.

    Returns:
        The PNG bytes.
    """
    missing = sorted(set(text) - set(_GLYPHS))
    if missing or not text:
        msg = f"digits_png draws digits only; got {text!r} (unsupported: {missing})"
        raise ValueError(msg)

    cells_wide = len(text) * _GLYPH_WIDTH + (len(text) - 1) * _GLYPH_GAP
    width = margin * 2 + cells_wide * scale
    height = margin * 2 + _GLYPH_HEIGHT * scale
    pixels = bytearray([PAPER]) * (width * height)

    for index, char in enumerate(text):
        left_cell = index * (_GLYPH_WIDTH + _GLYPH_GAP)
        for row, bits in enumerate(_GLYPHS[char]):
            for column, bit in enumerate(bits):
                if bit != "1":
                    continue
                x0 = margin + (left_cell + column) * scale
                y0 = margin + row * scale
                for y in range(y0, y0 + scale):
                    start = y * width + x0
                    pixels[start : start + scale] = bytes([INK]) * scale

    return _encode_grayscale_png(pixels, width, height)

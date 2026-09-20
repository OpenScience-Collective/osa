"""Draw a small bar chart as a PNG, with no image dependency.

Nothing in this repository draws images at runtime, so there is no matplotlib
or Pillow to lean on, and pulling either in as a test dependency to draw five
rectangles would be a poor trade. The encoder writes 8-bit grayscale (PNG color
type 0), one byte per pixel, with filter type 0 on every row: the smallest
thing the format allows that is still a valid PNG rather than a fixture that
happens to work.

A bar chart rather than a number, because of what two live runs showed. The
first fixture drew "734" in a 5x7 bitmap font; the model replied "724", then
"704" after the glyph was redrawn. The first and last digits were right both
times, so the picture was arriving and being read; five-pixel-wide glyphs are
simply not a reliable channel once an image has been downscaled on its way into
a model. Which bar is tallest does not degrade that way, and it is closer to
what this runtime will really be asked about.
"""

import struct
import zlib
from collections.abc import Sequence

INK = 0x00
PAPER = 0xFF

# The two charts the live transport test asks a model about, identical in every
# way except which bar is tallest and which is shortest. They live here, beside
# the encoder, so the offline suite can check the one property that makes the
# pair worth running (the guard in
# tests/test_core/test_tool_result_image_transport.py) without importing a
# module that is skipped whenever no API key is present.
#
# Four bars, not seven, because of what the third live run showed. On a
# seven-bar chart claude-haiku-4-5 reported bar 4 as the tallest when bar 5 was
# 1.0 and bar 4 was 0.30, while naming the shortest bar correctly and getting
# BOTH answers right on the other chart. A model perceiving nothing cannot do
# that, so the picture was arriving and being read; what it got wrong was the
# index, counting to five across seven bars. This test exists to measure
# whether a figure reaches the model, not how far the model can count, and a
# fixture that fails for the second reason cannot answer the first question.
# Four bars are counted reliably and the heights below are far apart, so a
# wrong answer means a wrong picture.
BAR_FIXTURES: dict[str, list[float]] = {
    "tallest_third": [0.35, 0.60, 1.0, 0.12],
    "tallest_first": [1.0, 0.45, 0.15, 0.70],
}


def tallest_and_shortest(heights: Sequence[float]) -> tuple[int, int]:
    """The bar positions, counting from the left starting at 1.

    Derived rather than written down beside the heights, so a fixture and the
    answer it expects cannot drift apart.
    """
    values = list(heights)
    return values.index(max(values)) + 1, values.index(min(values)) + 1


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


def _fill(pixels: bytearray, width: int, left: int, top: int, right: int, bottom: int) -> None:
    for y in range(top, bottom):
        pixels[y * width + left : y * width + right] = bytes([INK]) * (right - left)


def bar_chart_png(
    heights: Sequence[float],
    *,
    bar_width: int = 60,
    gap: int = 40,
    plot_height: int = 320,
    margin: int = 40,
) -> bytes:
    """Draw `heights` as black bars on white, sitting on an axis line.

    Args:
        heights: one value per bar, each in (0, 1], as a fraction of the plot
            height. Distinct values keep "which bar is tallest" unambiguous.
        bar_width: bar width in pixels.
        gap: space between bars in pixels.
        plot_height: height of the tallest possible bar, in pixels.
        margin: white border in pixels.

    Returns:
        The PNG bytes.
    """
    if not heights or any(not 0 < height <= 1 for height in heights):
        msg = f"heights must be non-empty and each within (0, 1]; got {heights!r}"
        raise ValueError(msg)

    count = len(heights)
    axis_thickness = 6
    width = margin * 2 + count * bar_width + (count - 1) * gap
    height = margin * 2 + plot_height + axis_thickness
    pixels = bytearray([PAPER]) * (width * height)

    baseline = margin + plot_height
    _fill(pixels, width, margin, baseline, width - margin, baseline + axis_thickness)

    for index, value in enumerate(heights):
        left = margin + index * (bar_width + gap)
        bar_top = baseline - round(value * plot_height)
        _fill(pixels, width, left, bar_top, left + bar_width, baseline)

    return _encode_grayscale_png(pixels, width, height)

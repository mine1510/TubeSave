"""Generate the TubeSave app icon: YouTube play-button shape, download arrow."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
EXT_ICONS = REPO / "browser-extension" / "icons"

RED_TOP = (255, 52, 52, 255)
RED_BOTTOM = (198, 0, 0, 255)


def _rounded_mask(size: int, box: tuple[int, int, int, int], radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    return mask


def _gradient(size: int, box: tuple[int, int, int, int]) -> Image.Image:
    x0, y0, x1, y1 = box
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    height = max(y1 - y0, 1)
    for y in range(y0, y1 + 1):
        t = (y - y0) / height
        color = (
            int(RED_TOP[0] + (RED_BOTTOM[0] - RED_TOP[0]) * t),
            int(RED_TOP[1] + (RED_BOTTOM[1] - RED_TOP[1]) * t),
            int(RED_TOP[2] + (RED_BOTTOM[2] - RED_TOP[2]) * t),
            255,
        )
        draw.line((x0, y, x1, y), fill=color)
    return layer


def _arrow_points(
    cx: float, cy: float, size: int
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    stem_w = size * 0.11
    stem_h = size * 0.24
    head_w = size * 0.36
    head_h = size * 0.24
    cy = cy + size * 0.015
    stem_top = cy - stem_h * 0.78
    stem_bot = cy + stem_h * 0.12
    stem = [
        (cx - stem_w / 2, stem_top),
        (cx + stem_w / 2, stem_top),
        (cx + stem_w / 2, stem_bot),
        (cx - stem_w / 2, stem_bot),
    ]
    head = [
        (cx, cy + head_h * 0.92),
        (cx - head_w / 2, stem_bot - size * 0.012),
        (cx + head_w / 2, stem_bot - size * 0.012),
    ]
    return stem, head


def render_master(canvas: int = 1024) -> Image.Image:
    """Landscape YouTube-style rounded rect on a transparent square."""
    width = int(canvas * 0.90)
    height = int(width * 11 / 16)
    x0 = (canvas - width) // 2
    y0 = (canvas - height) // 2
    x1 = x0 + width
    y1 = y0 + height
    radius = int(height * 0.28)
    box = (x0, y0, x1, y1)

    mask = _rounded_mask(canvas, box, radius)
    body = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    body.paste(_gradient(canvas, box), mask=mask)

    shadow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(box, radius=radius, fill=(90, 0, 0, 80))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=max(2, int(canvas * 0.028))))
    offset = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    offset.paste(shadow, (0, int(canvas * 0.016)))
    composed = Image.alpha_composite(offset, body)

    highlight = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    inset = int(canvas * 0.018)
    ImageDraw.Draw(highlight).rounded_rectangle(
        (x0 + inset, y0 + inset, x1 - inset, int(y0 + height * 0.40)),
        radius=max(1, int(radius * 0.65)),
        fill=(255, 255, 255, 34),
    )
    highlight.putalpha(ImageChops.multiply(highlight.split()[-1], mask))
    composed = Image.alpha_composite(composed, highlight)

    arrow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(arrow)
    stem, head = _arrow_points(canvas / 2, canvas / 2, min(width, height))
    draw.polygon(stem, fill=(255, 255, 255, 255))
    draw.polygon(head, fill=(255, 255, 255, 255))
    return Image.alpha_composite(composed, arrow)


def downscale(master: Image.Image, size: int) -> Image.Image:
    return master.resize((size, size), Image.Resampling.LANCZOS)


def save_ico(master: Image.Image, dest: Path) -> None:
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(dest, format="ICO", sizes=sizes)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    EXT_ICONS.mkdir(parents=True, exist_ok=True)

    huge = render_master(2048)
    master = huge.resize((1024, 1024), Image.Resampling.LANCZOS)
    master.save(ROOT / "tubesave.png", "PNG")
    save_ico(master, ROOT / "tubesave.ico")

    for side in (16, 32, 48, 64, 128, 256):
        out = downscale(master, side)
        out.save(ROOT / f"tubesave-{side}.png", "PNG")
        if side in {16, 32, 48, 128}:
            out.save(EXT_ICONS / f"icon{side}.png", "PNG")

    print("Wrote", ROOT / "tubesave.ico")
    print("Wrote", ROOT / "tubesave.png")
    print("Wrote extension icons in", EXT_ICONS)


if __name__ == "__main__":
    main()

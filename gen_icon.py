"""Rasterize the app logo into icon.ico, favicon.ico and logo.png.

The logo is simple geometry (rounded square, an X, a T, a centre dot), so we
draw it directly with Pillow at high resolution and downscale for crisp,
antialiased output — no SVG renderer needed.

Run:  python gen_icon.py
Outputs:
  assets/icon.ico          (multi-size, used for POS.exe + the installer)
  static/img/favicon.ico   (browser tab icon)
  static/img/logo.png      (256px, used in the app's top bar)
"""
import os
from PIL import Image, ImageDraw

VIEW = 220          # SVG viewBox size
SS = 8              # supersample factor for antialiasing
BG = "#0B1220"
BLUE = "#2563EB"
CYAN = "#22D3EE"
GRAY = "#94A3B8"
GREEN = "#22C55E"


def _scaled(p, size):
    return tuple(v * size / VIEW for v in p)


def _round_line(draw, p1, p2, color, width, size):
    """Line with round caps (Pillow lines are square-capped by default)."""
    x1, y1 = _scaled(p1, size)
    x2, y2 = _scaled(p2, size)
    w = width * size / VIEW
    draw.line([(x1, y1), (x2, y2)], fill=color, width=int(round(w)))
    r = w / 2
    for (x, y) in ((x1, y1), (x2, y2)):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


def render(size):
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded background square.
    radius = 48 * big / VIEW
    d.rounded_rectangle([0, 0, big - 1, big - 1], radius=radius, fill=BG)

    # X (two diagonals), T (vertical + top bar), centre dot.
    _round_line(d, (60, 60), (160, 160), BLUE, 10, big)
    _round_line(d, (160, 60), (60, 160), CYAN, 10, big)
    _round_line(d, (110, 55), (110, 165), GRAY, 8, big)
    _round_line(d, (85, 55), (135, 55), GRAY, 8, big)

    cx, cy = _scaled((110, 110), big)
    rr = 10 * big / VIEW
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=GREEN)

    return img.resize((size, size), Image.LANCZOS)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(here, "assets"), exist_ok=True)
    os.makedirs(os.path.join(here, "static", "img"), exist_ok=True)

    master = render(256)
    sizes = [256, 128, 64, 48, 32, 16]
    imgs = {s: render(s) for s in sizes}
    icon_images = [imgs[s] for s in sizes]

    ico_path = os.path.join(here, "assets", "icon.ico")
    master.save(ico_path, format="ICO",
                sizes=[(s, s) for s in sizes],
                append_images=icon_images)
    print("wrote", ico_path)

    fav_path = os.path.join(here, "static", "img", "favicon.ico")
    master.save(fav_path, format="ICO", sizes=[(32, 32), (16, 16)])
    print("wrote", fav_path)

    png_path = os.path.join(here, "static", "img", "logo.png")
    master.save(png_path, format="PNG")
    print("wrote", png_path)


if __name__ == "__main__":
    main()

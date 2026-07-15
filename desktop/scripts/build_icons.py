from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ASSETS = Path(__file__).resolve().parents[1] / "assets"


def build_icon() -> Image.Image:
    size = 1024
    image = Image.new("RGBA", (size, size), (244, 247, 251, 255))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            mix = (x + y) / (2 * size)
            pixels[x, y] = (int(58 + 25 * mix), int(112 + 53 * mix), int(181 - 12 * mix), 255)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((130, 130, 894, 894), radius=210, fill=(247, 250, 253, 246))
    draw.ellipse((262, 252, 482, 472), fill=(72, 115, 190, 255))
    draw.ellipse((565, 290, 752, 477), fill=(45, 158, 151, 255))
    draw.ellipse((400, 555, 637, 792), fill=(94, 95, 175, 255))
    draw.line((365, 365, 660, 382), fill=(76, 100, 150, 255), width=46)
    draw.line((373, 414, 505, 650), fill=(76, 100, 150, 255), width=46)
    draw.line((659, 415, 535, 655), fill=(76, 100, 150, 255), width=46)
    return image


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    icon = build_icon()
    icon.save(ASSETS / "icon.png")
    icon.save(
        ASSETS / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Desktop icons written to {ASSETS}")


if __name__ == "__main__":
    main()

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
source = Image.open(ROOT / "public" / "bingdu-logo.png").convert("RGBA")
pixels = source.load()
for y in range(source.height):
    for x in range(source.width):
        red, green, blue, _ = pixels[x, y]
        whiteness = min(red, green, blue)
        alpha = max(0, min(255, (255 - whiteness) * 8))
        pixels[x, y] = (red, green, blue, alpha)

canvas_size = max(source.size)
canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
canvas.alpha_composite(source, ((canvas_size - source.width) // 2, (canvas_size - source.height) // 2))
output = ROOT / "assets" / "bingdu.ico"
output.parent.mkdir(parents=True, exist_ok=True)
canvas.save(output, sizes=[(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)])
print(output)

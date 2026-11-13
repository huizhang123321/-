from PIL import Image, ImageDraw
from pathlib import Path

out = Path(__file__).resolve().parent / "assets" / "keihub.ico"
out.parent.mkdir(parents=True, exist_ok=True)
img = Image.new("RGBA", (256, 256), (17, 21, 28, 255))
d = ImageDraw.Draw(img)
d.ellipse((16, 16, 240, 240), fill=(37, 99, 235, 255))
d.text((92, 72), "K", fill=(255, 255, 255, 255))
img.save(out, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(out)

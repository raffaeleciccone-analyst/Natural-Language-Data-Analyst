"""Estrae il solo logo (marchi) dall'icona, rimuovendo il riquadro scuro e lo
sfondo esterno, e salva un PNG trasparente quadrato pronto come favicon.

Script accessorio: non serve a far girare l'app e ha dipendenze proprie
(Pillow, SciPy), dichiarate nell'extra 'tools' di pyproject.toml.

    pip install -e ".[tools]"
    python scripts/make_favicon.py
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SRC = str(ASSETS / "logo-source.png")
OUT = str(ASSETS / "favicon.png")
SIZE = 256  # lato del PNG finale

im = Image.open(SRC).convert("RGB")
rgb = np.asarray(im).astype(np.int16)
bright = rgb.mean(2)

# 1) Il riquadro scuro dell'icona: pixel scuri. Riempiendo i "buchi" ottengo la
#    sagoma piena del quadrato; i buchi riempiti sono esattamente i marchi interni.
dark = bright < 100
square = ndimage.binary_fill_holes(dark)
# Tengo solo la componente scura più grande (il riquadro), scartando l'ombra sparsa.
lbl, n = ndimage.label(square)
if n:
    biggest = 1 + int(np.argmax(ndimage.sum(np.ones_like(lbl), lbl, range(1, n + 1))))
    square = lbl == biggest

# 2) Marchi = interno del quadrato che NON è il riempimento scuro.
marks = square & ~dark

# 3) Alpha morbida per bordi anti-aliasati: 0 dove scuro, 1 dove chiaro, dentro
#    il quadrato. Fuori dal quadrato è sempre trasparente.
alpha = np.clip((bright - 90) / 70.0, 0, 1)
alpha = np.where(square, alpha, 0.0)
alpha = np.where(marks, np.maximum(alpha, 0.25), alpha)  # garantisce visibilità dei marchi
alpha_img = Image.fromarray((alpha * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))

out = Image.merge("RGBA", (*im.split(), alpha_img))

# 4) Ritaglio stretto ai marchi + margine, poi centro in un quadrato trasparente.
ys, xs = np.where(np.asarray(alpha_img) > 20)
pad = 10
y0, y1 = max(0, ys.min() - pad), min(out.height, ys.max() + pad)
x0, x1 = max(0, xs.min() - pad), min(out.width, xs.max() + pad)
crop = out.crop((x0, y0, x1, y1))

side = max(crop.width, crop.height)
canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
canvas.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2), crop)
canvas = canvas.resize((SIZE, SIZE), Image.LANCZOS)
canvas.save(OUT)
print("salvato", OUT, canvas.size)

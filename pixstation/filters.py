"""Raster filters (Pillow + NumPy) and a live-preview parameter dialog."""
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QSlider

from .document import IMAGE_FORMAT, MASK_FORMAT


# ---- QImage <-> PIL ----

def to_pil(img):
    img = img.convertToFormat(QImage.Format_RGBA8888)
    return Image.frombuffer("RGBA", (img.width(), img.height()), bytes(img.constBits()),
                            "raw", "RGBA", img.bytesPerLine(), 1).copy()


def to_qimage(im):
    im = im.convert("RGBA")
    data = im.tobytes()
    return QImage(data, im.width, im.height, 4 * im.width, QImage.Format_RGBA8888).convertToFormat(IMAGE_FORMAT)


def _on_rgb(fn):
    """Run fn on the colour channels only, keeping alpha untouched."""
    def wrapped(im, *args):
        alpha = im.getchannel("A")
        out = fn(im.convert("RGB"), *args).convert("RGB")
        out.putalpha(alpha)
        return out
    return wrapped


# ---- filters: (PIL RGBA, *params) -> PIL RGBA ----
# Spatial filters run on premultiplied "RGBa" so transparent pixels don't bleed dark edges.

def gaussian_blur(im, radius):
    return im.convert("RGBa").filter(ImageFilter.GaussianBlur(radius)).convert("RGBA")


def sharpen(im, radius, amount):
    return im.convert("RGBa").filter(ImageFilter.UnsharpMask(radius, amount, 2)).convert("RGBA")


@_on_rgb
def brightness_contrast(im, brightness, contrast):
    im = ImageEnhance.Brightness(im).enhance(1 + brightness / 100)
    return ImageEnhance.Contrast(im).enhance(1 + contrast / 100)


@_on_rgb
def hue_saturation(im, hue, saturation):
    hsv = np.array(im.convert("HSV"), dtype=np.int16)
    hsv[..., 0] = (hsv[..., 0] + round(hue * 256 / 360)) % 256
    hsv[..., 1] = np.clip(hsv[..., 1] * (1 + saturation / 100), 0, 255)
    return Image.fromarray(hsv.astype(np.uint8), "HSV")


@_on_rgb
def invert(im):
    return ImageOps.invert(im)


@_on_rgb
def grayscale(im):
    return ImageOps.grayscale(im)


# (menu label, function, [(param label, min, max, default), ...])
FILTERS = [
    ("Gaussian Blur…", gaussian_blur, [("Radius", 0, 100, 4)]),
    ("Sharpen…", sharpen, [("Radius", 1, 20, 2), ("Amount %", 0, 500, 150)]),
    ("Brightness/Contrast…", brightness_contrast, [("Brightness", -100, 100, 0), ("Contrast", -100, 100, 0)]),
    ("Hue/Saturation…", hue_saturation, [("Hue °", -180, 180, 0), ("Saturation", -100, 100, 0)]),
    ("Invert", invert, []),
    ("Grayscale", grayscale, []),
]


# ---- paint bucket helpers ----

def image_array(img):
    """(h, w, channels) uint8 copy: RGBA for layer images, 1 channel for masks."""
    if img.format() == MASK_FORMAT:
        a = np.frombuffer(bytes(img.constBits()), np.uint8).reshape(img.height(), img.bytesPerLine())
        return a[:, :img.width()][..., None].copy()
    img = img.convertToFormat(QImage.Format_RGBA8888)
    a = np.frombuffer(bytes(img.constBits()), np.uint8).reshape(img.height(), img.bytesPerLine())
    return a[:, :img.width() * 4].reshape(img.height(), img.width(), 4).copy()


def flood_region(img, x, y, tolerance):
    """Boolean (h, w) array: pixels connected to (x, y) within tolerance (0–255 per channel)."""
    arr = image_array(img).astype(np.int16)
    similar = np.abs(arr - arr[y, x]).max(axis=2) <= tolerance
    # Flood-fill a 0/255 image with 128: only the connected similar area becomes 128.
    # .copy(): an image made by fromarray shares the array's memory, and floodfill
    # silently leaves such an image unchanged (seen with Pillow 13).
    label = Image.fromarray(similar.astype(np.uint8) * 255, "L").copy()
    ImageDraw.floodfill(label, (x, y), 128, thresh=0)
    return np.array(label) == 128


def region_fill(img, region, color):
    """Image to draw over img: color inside region, transparent elsewhere."""
    rgba = np.zeros((img.height(), img.width(), 4), np.uint8)
    rgba[region] = (color.red(), color.green(), color.blue(), color.alpha())
    return to_qimage(Image.fromarray(rgba, "RGBA"))


def region_mask(mask, region, value):
    """Copy of mask with the region set to value (0 hides, 255 shows)."""
    arr = image_array(mask)[..., 0]
    arr[region] = value
    arr = np.ascontiguousarray(arr)
    return QImage(arr.tobytes(), arr.shape[1], arr.shape[0], arr.shape[1], MASK_FORMAT).copy()


def apply_filter(image, fn, params, selection=None):
    """Return a new QImage with fn applied, limited to the selection path if given."""
    filtered = to_qimage(fn(to_pil(image), *params))
    if selection is None:
        return filtered
    out = QImage(image)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setClipPath(selection)
    p.setCompositionMode(QPainter.CompositionMode_Source)
    p.drawImage(0, 0, filtered)
    p.end()
    return out


class FilterDialog(QDialog):
    """Sliders for a filter's parameters; calls preview(params) as they change."""

    def __init__(self, parent, title, params, preview):
        super().__init__(parent)
        self.setWindowTitle(title.rstrip("…"))
        self.preview = preview
        self.sliders = []
        form = QFormLayout(self)
        for label, lo, hi, default in params:
            s = QSlider(Qt.Horizontal, minimum=lo, maximum=hi, value=default)
            s.setMinimumWidth(260)
            value = QLabel(str(default), minimumWidth=36)
            s.valueChanged.connect(lambda v, l=value: l.setText(str(v)))
            s.valueChanged.connect(lambda _: self._timer.start())
            row = QHBoxLayout()
            row.addWidget(s)
            row.addWidget(value)
            form.addRow(label, row)
            self.sliders.append(s)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        # debounce: big images take a moment to filter, so wait until the slider pauses
        self._timer = QTimer(self, singleShot=True, interval=120)
        self._timer.timeout.connect(lambda: self.preview(self.values()))

    def values(self):
        return [s.value() for s in self.sliders]

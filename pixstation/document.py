"""Document model: layers, shapes, rendering and undoable commands."""
from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (QBrush, QColor, QImage, QPainter, QPainterPath, QPen,
                           QUndoCommand)
from PySide6.QtSvg import QSvgGenerator

IMAGE_FORMAT = QImage.Format_ARGB32_Premultiplied

# Blend mode key → (menu label, Qt composition mode). Keys match OpenRaster's "svg:<key>".
BLEND_MODES = {
    "src-over": ("Normal", QPainter.CompositionMode_SourceOver),
    "multiply": ("Multiply", QPainter.CompositionMode_Multiply),
    "screen": ("Screen", QPainter.CompositionMode_Screen),
    "overlay": ("Overlay", QPainter.CompositionMode_Overlay),
    "darken": ("Darken", QPainter.CompositionMode_Darken),
    "lighten": ("Lighten", QPainter.CompositionMode_Lighten),
    "color-dodge": ("Color Dodge", QPainter.CompositionMode_ColorDodge),
    "color-burn": ("Color Burn", QPainter.CompositionMode_ColorBurn),
    "hard-light": ("Hard Light", QPainter.CompositionMode_HardLight),
    "soft-light": ("Soft Light", QPainter.CompositionMode_SoftLight),
    "difference": ("Difference", QPainter.CompositionMode_Difference),
    "exclusion": ("Exclusion", QPainter.CompositionMode_Exclusion),
}


def blank_image(w, h, color=Qt.transparent):
    img = QImage(w, h, IMAGE_FORMAT)
    img.fill(color)
    return img


class Layer:
    def __init__(self, name):
        self.name = name
        self.visible = True
        self.opacity = 1.0
        self.blend = "src-over"


class RasterLayer(Layer):
    kind = "raster"

    def __init__(self, name, w, h, image=None):
        super().__init__(name)
        self.image = image if image is not None else blank_image(w, h)

    def paint(self, p):
        p.drawImage(0, 0, self.image)


@dataclass(eq=False)  # identity equality: two identical shapes are still different shapes
class Shape:
    path: QPainterPath
    stroke: QColor
    width: float
    fill: QColor | None = None

    def paint(self, p):
        if self.width > 0:
            p.setPen(QPen(self.stroke, self.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        else:
            p.setPen(Qt.NoPen)
        p.setBrush(QBrush(self.fill) if self.fill is not None else Qt.NoBrush)
        p.drawPath(self.path)


class VectorLayer(Layer):
    kind = "vector"

    def __init__(self, name):
        super().__init__(name)
        self.shapes = []

    def paint(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        for s in self.shapes:
            s.paint(p)


class Document:
    def __init__(self, w, h):
        self.width, self.height = w, h
        self.layers = []  # index 0 = bottom

    @classmethod
    def new_default(cls, w=1200, h=800):
        doc = cls(w, h)
        doc.layers.append(RasterLayer("Background", w, h, blank_image(w, h, Qt.white)))
        doc.layers.append(VectorLayer("Vector 1"))
        return doc

    @classmethod
    def from_image(cls, image):
        doc = cls(image.width(), image.height())
        doc.layers.append(RasterLayer("Image", 0, 0, image.convertToFormat(IMAGE_FORMAT)))
        return doc

    def layer_image(self, layer):
        """The layer's pixels at document size (vector layers are rasterised)."""
        if layer.kind == "raster":
            return layer.image
        img = blank_image(self.width, self.height)
        p = QPainter(img)
        layer.paint(p)
        p.end()
        return img

    def render(self, p, keep_vectors=False):
        """Composite visible layers. keep_vectors=True (SVG export) draws vector layers as
        paths even when opacity/blend would need a rasterised pass to be exact."""
        for layer in self.layers:
            if not layer.visible:
                continue
            p.save()
            p.setOpacity(layer.opacity)
            p.setCompositionMode(BLEND_MODES[layer.blend][1])
            plain = layer.opacity == 1 and layer.blend == "src-over"
            if layer.kind == "vector" and (plain or keep_vectors):
                layer.paint(p)
            else:  # composite the layer as one image so shapes don't blend with each other
                p.drawImage(0, 0, self.layer_image(layer))
            p.restore()

    def flatten(self):
        img = blank_image(self.width, self.height)
        p = QPainter(img)
        self.render(p)
        p.end()
        return img

    def export_svg(self, path):
        gen = QSvgGenerator()
        gen.setFileName(path)
        gen.setSize(QSize(self.width, self.height))
        gen.setViewBox(QRect(0, 0, self.width, self.height))
        gen.setTitle("PixStation export")
        p = QPainter(gen)
        # vector layers become <path>; raster layers are embedded as <image>.
        # SVG output keeps opacity but not blend modes (QSvgGenerator limitation).
        self.render(p, keep_vectors=True)
        p.end()


# ---- Undo commands (pushed onto a QUndoStack; push() calls redo()) ----

class ImageEdit(QUndoCommand):
    # chisle: stores whole-image snapshots (QImage is copy-on-write, so cheap until
    # painted). Switch to dirty-rect tiles if memory gets tight on big canvases.
    def __init__(self, layer, before, after, text):
        super().__init__(text)
        self.layer, self.before, self.after = layer, before, after

    def redo(self):
        self.layer.image = QImage(self.after)

    def undo(self):
        self.layer.image = QImage(self.before)


class AddShape(QUndoCommand):
    def __init__(self, layer, shape, text="Add shape"):
        super().__init__(text)
        self.layer, self.shape = layer, shape

    def redo(self):
        self.layer.shapes.append(self.shape)

    def undo(self):
        self.layer.shapes.remove(self.shape)


class RemoveShape(QUndoCommand):
    def __init__(self, layer, shape):
        super().__init__("Delete shape")
        self.layer, self.shape = layer, shape
        self.index = layer.shapes.index(shape)

    def redo(self):
        self.layer.shapes.remove(self.shape)

    def undo(self):
        self.layer.shapes.insert(self.index, self.shape)


class SetAttrs(QUndoCommand):
    """Set attributes on any object (shape or layer). Consecutive edits of the same
    attributes on the same object merge into one undo step (e.g. dragging a slider)."""
    ID = 1

    def __init__(self, obj, after, text, merge=False):
        super().__init__(text)
        self.obj, self.after, self.merge = obj, dict(after), merge
        self.before = {k: getattr(obj, k) for k in after}

    def id(self):
        return self.ID if self.merge else -1

    def mergeWith(self, other):
        if other.obj is not self.obj or other.after.keys() != self.after.keys():
            return False
        self.after = other.after
        return True

    def redo(self):
        for k, v in self.after.items():
            setattr(self.obj, k, v)

    def undo(self):
        for k, v in self.before.items():
            setattr(self.obj, k, v)


class AddLayer(QUndoCommand):
    def __init__(self, doc, layer, index):
        super().__init__(f"Add layer {layer.name}")
        self.doc, self.layer, self.index = doc, layer, index

    def redo(self):
        self.doc.layers.insert(self.index, self.layer)

    def undo(self):
        self.doc.layers.remove(self.layer)


class RemoveLayer(QUndoCommand):
    def __init__(self, doc, index):
        super().__init__(f"Delete layer {doc.layers[index].name}")
        self.doc, self.index, self.layer = doc, index, doc.layers[index]

    def redo(self):
        self.doc.layers.remove(self.layer)

    def undo(self):
        self.doc.layers.insert(self.index, self.layer)


class MoveLayer(QUndoCommand):
    def __init__(self, doc, src, dst):
        super().__init__("Reorder layers")
        self.doc, self.src, self.dst = doc, src, dst

    def redo(self):
        self.doc.layers.insert(self.dst, self.doc.layers.pop(self.src))

    def undo(self):
        self.doc.layers.insert(self.src, self.doc.layers.pop(self.dst))

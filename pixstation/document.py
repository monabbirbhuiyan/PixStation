"""Document model: layer tree, shapes, masks, rendering and undoable commands."""
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRect, QSize, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen,
                           QTransform, QUndoCommand)
from PySide6.QtSvg import QSvgGenerator

IMAGE_FORMAT = QImage.Format_ARGB32_Premultiplied
MASK_FORMAT = QImage.Format_Alpha8   # mask value = alpha: 255 shows the layer, 0 hides it
MASK_SHOW = QColor(0, 0, 0, 255)

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


def blank_mask(w, h):
    m = QImage(w, h, MASK_FORMAT)
    m.fill(MASK_SHOW)
    return m


def shifted(img, dx, dy, w=None, h=None):
    """Copy of img moved by (dx, dy) on a canvas of w×h. Uncovered pixels are transparent
    for layer images and fully visible for masks."""
    w = img.width() if w is None else w
    h = img.height() if h is None else h
    out = QImage(w, h, img.format())
    out.fill(MASK_SHOW if img.format() == MASK_FORMAT else Qt.transparent)
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode_Source)
    p.drawImage(round(dx), round(dy), img)
    p.end()
    return out


def mask_to_gray(mask):
    """Alpha8 mask → Grayscale8 (white = visible), e.g. for PNG files and thumbnails."""
    return QImage(bytes(mask.constBits()), mask.width(), mask.height(), mask.bytesPerLine(),
                  QImage.Format_Grayscale8).copy()


def gray_to_mask(gray):
    gray = gray.convertToFormat(QImage.Format_Grayscale8)
    return QImage(bytes(gray.constBits()), gray.width(), gray.height(), gray.bytesPerLine(),
                  MASK_FORMAT).copy()


class Layer:
    def __init__(self, name):
        self.name = name
        self.visible = True
        self.opacity = 1.0
        self.blend = "src-over"
        self.mask = None  # QImage (MASK_FORMAT) or None

    def _clone_base(self, other):
        other.visible, other.opacity, other.blend = self.visible, self.opacity, self.blend
        other.mask = QImage(self.mask) if self.mask is not None else None
        return other


class RasterLayer(Layer):
    kind = "raster"

    def __init__(self, name, w, h, image=None):
        super().__init__(name)
        self.image = image if image is not None else blank_image(w, h)

    def paint(self, p):
        p.drawImage(0, 0, self.image)

    def clone(self, name):
        return self._clone_base(RasterLayer(name, 0, 0, QImage(self.image)))


def text_path(text):
    """Outline path for a text dict {"text", "font", "x", "y", "transform"}."""
    font = QFont()
    font.fromString(text["font"])
    path = QPainterPath()
    path.addText(QPointF(text["x"], text["y"]), font, text["text"])
    return text["transform"].map(path)


@dataclass(eq=False)  # identity equality: two identical shapes are still different shapes
class Shape:
    path: QPainterPath
    stroke: QColor
    width: float
    fill: QColor | None = None
    text: dict | None = None  # set for text shapes so they stay re-editable

    def paint(self, p):
        if self.width > 0:
            p.setPen(QPen(self.stroke, self.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        else:
            p.setPen(Qt.NoPen)
        p.setBrush(QBrush(self.fill) if self.fill is not None else Qt.NoBrush)
        p.drawPath(self.path)

    def clone(self):
        return Shape(QPainterPath(self.path), QColor(self.stroke), self.width,
                     QColor(self.fill) if self.fill is not None else None,
                     dict(self.text) if self.text else None)

    def transformed(self, t):
        """Attribute values for this shape after applying QTransform t (for SetAttrs)."""
        out = {"path": t.map(self.path)}
        if self.text:
            out["text"] = {**self.text, "transform": self.text["transform"] * t}
        return out


class VectorLayer(Layer):
    kind = "vector"

    def __init__(self, name):
        super().__init__(name)
        self.shapes = []

    def paint(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        for s in self.shapes:
            s.paint(p)

    def clone(self, name):
        layer = VectorLayer(name)
        layer.shapes = [s.clone() for s in self.shapes]
        return self._clone_base(layer)


class GroupLayer(Layer):
    kind = "group"

    def __init__(self, name, children=None):
        super().__init__(name)
        self.children = children if children is not None else []  # index 0 = bottom
        self.expanded = True

    def clone(self, name):
        group = GroupLayer(name, [c.clone(c.name) for c in self.children])
        return self._clone_base(group)


class Document:
    def __init__(self, w, h):
        self.width, self.height = w, h
        self.layers = []  # index 0 = bottom; groups hold their own children lists

    @classmethod
    def new_default(cls, w=1200, h=800, background=Qt.white):
        doc = cls(w, h)
        bg = blank_image(w, h, background) if background is not None else None
        doc.layers.append(RasterLayer("Background", w, h, bg))
        doc.layers.append(VectorLayer("Vector 1"))
        return doc

    @classmethod
    def from_image(cls, image):
        doc = cls(image.width(), image.height())
        doc.layers.append(RasterLayer("Image", 0, 0, image.convertToFormat(IMAGE_FORMAT)))
        return doc

    # ---- tree helpers ----
    def walk(self, layers=None):
        """Every layer, depth first (groups before their children)."""
        for layer in self.layers if layers is None else layers:
            yield layer
            if layer.kind == "group":
                yield from self.walk(layer.children)

    def parent_list(self, layer, layers=None):
        layers = self.layers if layers is None else layers
        if layer in layers:
            return layers
        for l in layers:
            if l.kind == "group":
                found = self.parent_list(layer, l.children)
                if found is not None:
                    return found
        return None

    def parent_group(self, layer):
        return next((g for g in self.walk() if g.kind == "group" and layer in g.children), None)

    def contains(self, layer):
        return layer is not None and self.parent_list(layer) is not None

    # ---- rendering ----
    def layer_image(self, layer, masked=True):
        """The layer's pixels at document size: vector layers rasterised, groups composited,
        mask applied unless masked=False."""
        if layer.kind == "raster":
            img = layer.image
        else:
            img = blank_image(self.width, self.height)
            p = QPainter(img)
            if layer.kind == "vector":
                layer.paint(p)
            else:
                self._composite(layer.children, p)
            p.end()
        if masked and layer.mask is not None:
            img = QImage(img)
            p = QPainter(img)
            p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            p.drawImage(0, 0, layer.mask)
            p.end()
        return img

    def _composite(self, layers, p, keep_vectors=False):
        for layer in layers:
            if not layer.visible:
                continue
            p.save()
            p.setOpacity(layer.opacity)
            p.setCompositionMode(BLEND_MODES[layer.blend][1])
            plain = layer.opacity == 1 and layer.blend == "src-over"
            if layer.kind == "vector" and layer.mask is None and (plain or keep_vectors):
                layer.paint(p)  # direct: stays vector in SVG export
            elif layer.kind == "group" and keep_vectors and layer.mask is None and layer.blend == "src-over":
                self._composite(layer.children, p, keep_vectors)
            else:  # as one image, so a layer's parts don't blend with each other
                p.drawImage(0, 0, self.layer_image(layer))
            p.restore()

    def render(self, p, keep_vectors=False):
        """Composite visible layers. keep_vectors=True (SVG export) draws vector layers as
        paths even when opacity/blend would need a rasterised pass to be exact."""
        self._composite(self.layers, p, keep_vectors)

    def flatten(self, layers=None):
        img = blank_image(self.width, self.height)
        p = QPainter(img)
        self._composite(self.layers if layers is None else layers, p)
        p.end()
        return img

    def export_svg(self, path):
        gen = QSvgGenerator()
        gen.setFileName(path)
        gen.setSize(QSize(self.width, self.height))
        gen.setViewBox(QRect(0, 0, self.width, self.height))
        gen.setTitle("PixStation export")
        p = QPainter(gen)
        # vector layers become <path>; raster layers and masked layers are embedded as <image>.
        # SVG output keeps opacity but not blend modes (QSvgGenerator limitation).
        self.render(p, keep_vectors=True)
        p.end()


# ---- Undo commands (pushed onto a QUndoStack; push() calls redo()) ----

class ImageEdit(QUndoCommand):
    # Note: stores whole-image snapshots (QImage is copy-on-write, so cheap until
    # painted). Switch to dirty-rect tiles if memory gets tight on big canvases.
    def __init__(self, layer, before, after, text, attr="image"):
        super().__init__(text)
        self.layer, self.before, self.after, self.attr = layer, before, after, attr

    def redo(self):
        setattr(self.layer, self.attr, QImage(self.after))

    def undo(self):
        setattr(self.layer, self.attr, QImage(self.before))


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


class Splice(QUndoCommand):
    """Replace items[index:index+len(old)] with new. Covers add, delete, group, ungroup,
    merge and duplicate for layers (and works on any list)."""

    def __init__(self, items, index, old, new, text):
        super().__init__(text)
        self.items, self.index, self.old, self.new = items, index, list(old), list(new)

    def redo(self):
        self.items[self.index:self.index + len(self.old)] = self.new

    def undo(self):
        self.items[self.index:self.index + len(self.new)] = self.old


class Relocate(QUndoCommand):
    """Move one item from one list position to another (same or different list)."""

    def __init__(self, item, src, dst, dst_index, text):
        super().__init__(text)
        self.item, self.src, self.dst, self.dst_index = item, src, dst, dst_index
        self.src_index = src.index(item)

    def redo(self):
        self.src.remove(self.item)
        self.dst.insert(self.dst_index, self.item)

    def undo(self):
        self.dst.remove(self.item)
        self.src.insert(self.src_index, self.item)


class ResizeCanvas(QUndoCommand):
    """Crop or extend the canvas to rect (in current document coordinates)."""

    def __init__(self, doc, rect, text):
        super().__init__(text)
        self.doc = doc
        self.sizes = ((doc.width, doc.height), (rect.width(), rect.height()))
        dx, dy = -rect.x(), -rect.y()
        w, h = rect.width(), rect.height()
        move = QTransform().translate(dx, dy)
        self.changes = []  # (obj, attr, before, after)
        for layer in doc.walk():
            if layer.kind == "raster":
                self.changes.append((layer, "image", layer.image, shifted(layer.image, dx, dy, w, h)))
            if layer.mask is not None:
                self.changes.append((layer, "mask", layer.mask, shifted(layer.mask, dx, dy, w, h)))
            if layer.kind == "vector":
                for s in layer.shapes:
                    for k, v in s.transformed(move).items():
                        self.changes.append((s, k, getattr(s, k), v))

    def _apply(self, which):
        for obj, attr, *values in self.changes:
            setattr(obj, attr, values[which])
        self.doc.width, self.doc.height = self.sizes[which]

    def redo(self):
        self._apply(1)

    def undo(self):
        self._apply(0)

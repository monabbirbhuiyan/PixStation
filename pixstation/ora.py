"""OpenRaster (.ora) project files: https://www.openraster.org/

Layout: ZIP with an uncompressed `mimetype` entry first, `stack.xml`, one PNG per
layer in `data/`, plus `mergedimage.png` and `Thumbnails/thumbnail.png`.

Layer groups are nested <stack> elements, as the spec defines.

Each layer's PNG is what it looks like (vector layers rasterised, masks applied), so other
OpenRaster apps (Krita, GIMP, MyPaint) show the right picture. PixStation keeps the editable
parts in extension attributes that other apps ignore:
  pixstation:vector    data/<n>.json, the shapes of a vector layer
  pixstation:mask      data/<n>_mask.png, the layer mask (grayscale, white = visible)
  pixstation:unmasked  data/<n>_unmasked.png, a masked raster layer's own pixels
"""
import itertools
import json
import os
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QTransform

from .document import (BLEND_MODES, IMAGE_FORMAT, Document, GroupLayer, RasterLayer, Shape,
                       VectorLayer, blank_image, gray_to_mask, mask_to_gray)

PIXSTATION_NS = "urn:pixstation:ora"
VECTOR_ATTR = f"{{{PIXSTATION_NS}}}vector"
MASK_ATTR = f"{{{PIXSTATION_NS}}}mask"
UNMASKED_ATTR = f"{{{PIXSTATION_NS}}}unmasked"


def _png_bytes(img):
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


# ---- shape <-> JSON ----

def _path_to_list(path):
    return [[path.elementAt(i).type.value, path.elementAt(i).x, path.elementAt(i).y]
            for i in range(path.elementCount())]


def _path_from_list(elements):
    path = QPainterPath()
    i = 0
    while i < len(elements):
        kind, x, y = elements[i]
        if kind == QPainterPath.MoveToElement.value:
            path.moveTo(x, y)
        elif kind == QPainterPath.LineToElement.value:
            path.lineTo(x, y)
        elif kind == QPainterPath.CurveToElement.value:  # followed by two CurveToData points
            (_, x2, y2), (_, x3, y3) = elements[i + 1], elements[i + 2]
            path.cubicTo(QPointF(x, y), QPointF(x2, y2), QPointF(x3, y3))
            i += 2
        i += 1
    return path


def _shape_to_dict(s):
    d = {"path": _path_to_list(s.path), "stroke": s.stroke.name(QColor.HexArgb),
         "width": s.width, "fill": s.fill.name(QColor.HexArgb) if s.fill is not None else None}
    if s.text:
        t = s.text["transform"]
        d["text"] = {**s.text, "transform": [t.m11(), t.m12(), t.m13(), t.m21(), t.m22(), t.m23(),
                                             t.m31(), t.m32(), t.m33()]}
    return d


def _shape_from_dict(d):
    text = d.get("text")
    if text:
        text = {**text, "transform": QTransform(*text["transform"])}
    return Shape(_path_from_list(d["path"]), QColor(d["stroke"]), float(d["width"]),
                 QColor(d["fill"]) if d.get("fill") else None, text)


# ---- save / load ----

def save_ora(doc, path):
    ET.register_namespace("pixstation", PIXSTATION_NS)
    image = ET.Element("image", {"version": "0.0.5", "w": str(doc.width), "h": str(doc.height)})
    files = {}
    numbers = itertools.count()

    def write(layers, parent):
        for layer in layers:  # ours are bottom-first; OpenRaster lists the top first
            n = next(numbers)
            attrs = {"name": layer.name, "visibility": "visible" if layer.visible else "hidden",
                     "opacity": f"{layer.opacity:.3f}", "composite-op": f"svg:{layer.blend}"}
            if layer.kind == "group":
                el = ET.Element("stack", attrs)
                write(layer.children, el)
            else:
                attrs.update({"src": f"data/layer{n}.png", "x": "0", "y": "0"})
                files[attrs["src"]] = _png_bytes(doc.layer_image(layer))
                if layer.kind == "vector":
                    attrs[VECTOR_ATTR] = f"data/layer{n}.json"
                    files[attrs[VECTOR_ATTR]] = json.dumps(
                        {"shapes": [_shape_to_dict(s) for s in layer.shapes]}).encode()
                elif layer.mask is not None:
                    attrs[UNMASKED_ATTR] = f"data/layer{n}_unmasked.png"
                    files[attrs[UNMASKED_ATTR]] = _png_bytes(layer.image)
                el = ET.Element("layer", attrs)
            if layer.mask is not None:
                el.set(MASK_ATTR, f"data/layer{n}_mask.png")
                files[el.get(MASK_ATTR)] = _png_bytes(mask_to_gray(layer.mask))
            parent.insert(0, el)

    write(doc.layers, ET.SubElement(image, "stack"))

    merged = doc.flatten()
    files["mergedimage.png"] = _png_bytes(merged)
    files["Thumbnails/thumbnail.png"] = _png_bytes(
        merged.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    files["stack.xml"] = ET.tostring(image, encoding="utf-8", xml_declaration=True)

    # Write to a temp file, then swap in, so a failed save never corrupts an existing file.
    fd, tmp = tempfile.mkstemp(suffix=".ora", dir=os.path.dirname(os.path.abspath(path)))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(zipfile.ZipInfo("mimetype"), "image/openraster", zipfile.ZIP_STORED)
            for name, data in files.items():
                z.writestr(name, data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _read_png(z, name):
    img = QImage.fromData(z.read(name), "PNG")
    if img.isNull():
        raise ValueError(f"unreadable image {name}")
    return img


def load_ora(path):
    """Read an .ora file. Raises ValueError for files that are not valid OpenRaster."""
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("stack.xml"))
            doc = Document(int(root.get("w")), int(root.get("h")))

            def read(stack):
                layers = []
                for el in reversed(list(stack)):  # file is top-first, we are bottom-first
                    name = el.get("name", "Layer")
                    if el.tag == "stack":
                        # Note: offsets of nested stacks (rare) are ignored.
                        layer = GroupLayer(name, read(el))
                    elif el.tag != "layer":
                        continue  # e.g. <text> elements from other apps
                    elif el.get(VECTOR_ATTR):
                        layer = VectorLayer(name)
                        data = json.loads(z.read(el.get(VECTOR_ATTR)))
                        layer.shapes = [_shape_from_dict(d) for d in data["shapes"]]
                    else:
                        src = _read_png(z, el.get(UNMASKED_ATTR) or el.get("src"))
                        img = blank_image(doc.width, doc.height)
                        p = QPainter(img)
                        p.drawImage(int(float(el.get("x", 0))), int(float(el.get("y", 0))),
                                    src.convertToFormat(IMAGE_FORMAT))
                        p.end()
                        layer = RasterLayer(name, 0, 0, img)
                    if el.get(MASK_ATTR):
                        mask = gray_to_mask(_read_png(z, el.get(MASK_ATTR)))
                        if mask.size() != img_size:
                            raise ValueError(f"mask {el.get(MASK_ATTR)} has the wrong size")
                        layer.mask = mask
                    layer.visible = el.get("visibility", "visible") != "hidden"
                    layer.opacity = min(max(float(el.get("opacity", 1)), 0.0), 1.0)
                    blend = el.get("composite-op", "svg:src-over").removeprefix("svg:")
                    layer.blend = blend if blend in BLEND_MODES else "src-over"  # unknown → Normal
                    layers.append(layer)
                return layers

            img_size = QSize(doc.width, doc.height)
            top = root.find("stack")
            if top is None:
                raise ValueError("stack.xml has no <stack>")
            doc.layers = read(top)
            return doc
    except (zipfile.BadZipFile, KeyError, ET.ParseError, TypeError, ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"Not a valid OpenRaster file: {e}") from e

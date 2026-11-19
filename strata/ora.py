"""OpenRaster (.ora) project files: https://www.openraster.org/

Layout: ZIP with an uncompressed `mimetype` entry first, `stack.xml`, one PNG per
layer in `data/`, plus `mergedimage.png` and `Thumbnails/thumbnail.png`.

Vector layers are also written as PNG, so other OpenRaster apps (Krita, GIMP, MyPaint)
show them. Their editable shapes go in `data/<n>.json`, referenced by the extension
attribute `strata:vector`; other apps ignore it.
"""
import json
import os
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

from .document import BLEND_MODES, Document, IMAGE_FORMAT, RasterLayer, Shape, VectorLayer, blank_image

STRATA_NS = "https://strata.local/ns/ora"
VECTOR_ATTR = f"{{{STRATA_NS}}}vector"


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
    return {"path": _path_to_list(s.path), "stroke": s.stroke.name(QColor.HexArgb),
            "width": s.width, "fill": s.fill.name(QColor.HexArgb) if s.fill is not None else None}


def _shape_from_dict(d):
    return Shape(_path_from_list(d["path"]), QColor(d["stroke"]), float(d["width"]),
                 QColor(d["fill"]) if d.get("fill") else None)


# ---- save / load ----

def save_ora(doc, path):
    ET.register_namespace("strata", STRATA_NS)
    image = ET.Element("image", {"version": "0.0.5", "w": str(doc.width), "h": str(doc.height)})
    stack = ET.SubElement(image, "stack")
    files = {}
    for n, layer in enumerate(doc.layers):
        attrs = {"name": layer.name, "src": f"data/layer{n}.png", "x": "0", "y": "0",
                 "visibility": "visible" if layer.visible else "hidden",
                 "opacity": f"{layer.opacity:.3f}", "composite-op": f"svg:{layer.blend}"}
        files[attrs["src"]] = _png_bytes(doc.layer_image(layer))
        if layer.kind == "vector":
            attrs[VECTOR_ATTR] = f"data/layer{n}.json"
            files[attrs[VECTOR_ATTR]] = json.dumps(
                {"shapes": [_shape_to_dict(s) for s in layer.shapes]}).encode()
        stack.insert(0, ET.Element("layer", attrs))  # OpenRaster lists the top layer first

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


def load_ora(path):
    """Read an .ora file. Raises ValueError for files that are not valid OpenRaster."""
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("stack.xml"))
            doc = Document(int(root.get("w")), int(root.get("h")))
            # chisle: nested stacks (groups) are flattened into one list; group offsets,
            # group opacity and group blend modes are ignored until Strata has groups.
            for el in reversed(list(root.iter("layer"))):  # file is top-first, we are bottom-first
                name = el.get("name", "Layer")
                if el.get(VECTOR_ATTR):
                    layer = VectorLayer(name)
                    data = json.loads(z.read(el.get(VECTOR_ATTR)))
                    layer.shapes = [_shape_from_dict(d) for d in data["shapes"]]
                else:
                    src = QImage.fromData(z.read(el.get("src")), "PNG")
                    if src.isNull():
                        raise ValueError(f"unreadable layer image {el.get('src')}")
                    img = blank_image(doc.width, doc.height)
                    p = QPainter(img)
                    p.drawImage(int(float(el.get("x", 0))), int(float(el.get("y", 0))),
                                src.convertToFormat(IMAGE_FORMAT))
                    p.end()
                    layer = RasterLayer(name, 0, 0, img)
                layer.visible = el.get("visibility", "visible") != "hidden"
                layer.opacity = min(max(float(el.get("opacity", 1)), 0.0), 1.0)
                blend = el.get("composite-op", "svg:src-over").removeprefix("svg:")
                layer.blend = blend if blend in BLEND_MODES else "src-over"  # unknown ops → Normal
                doc.layers.append(layer)
            return doc
    except (zipfile.BadZipFile, KeyError, ET.ParseError, TypeError, ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"Not a valid OpenRaster file: {e}") from e

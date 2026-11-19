"""Canvas widget: draws the document and turns mouse input into tool actions."""
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPainterPathStroker, QPen, QPixmap, QTransform)
from PySide6.QtWidgets import QApplication, QInputDialog, QWidget

from .document import (AddShape, ImageEdit, Relocate, RemoveShape, SetAttrs, Shape,
                       shifted, text_path)
from .filters import flood_region, region_fill, region_mask

RASTER_TOOLS = {"brush", "eraser", "bucket", "gradient"}  # paint the layer image or its mask
VECTOR_TOOLS = {"select", "direct", "rect", "ellipse", "pen", "text"}
SELECTION_TOOLS = {"marquee", "lasso"}  # pixel selections; work on any layer
CLOSE_RADIUS = 8   # screen px: clicking this close to the first pen point closes the path
HANDLE = 7         # screen px: size of shape resize handles / path points
HIT_TOL = 4        # screen px: extra slack when clicking thin shapes
BLUE = QColor(0, 120, 255)


def _checker():
    pm = QPixmap(16, 16)
    pm.fill(QColor(255, 255, 255))
    p = QPainter(pm)
    p.fillRect(0, 0, 8, 8, QColor(204, 204, 204))
    p.fillRect(8, 8, 8, 8, QColor(204, 204, 204))
    p.end()
    return pm


def bezier_path(pts, closed):
    """pts: [[anchor, out_handle_or_None], ...]. The incoming handle mirrors the outgoing one."""
    path = QPainterPath(pts[0][0])
    segs = list(zip(pts, pts[1:])) + ([(pts[-1], pts[0])] if closed else [])
    for (a, ha), (b, hb) in segs:
        if ha is None and hb is None:
            path.lineTo(b)
        else:
            path.cubicTo(ha if ha is not None else a, b * 2 - hb if hb is not None else b, b)
    if closed:
        path.closeSubpath()
    return path


def point_roles(path):
    """'anchor' or 'control' for each path element (a cubic is control, control, anchor)."""
    roles, i, n = [], 0, path.elementCount()
    while i < n:
        if path.elementAt(i).type == QPainterPath.CurveToElement:
            roles += ["control", "control", "anchor"]
            i += 3
        else:
            roles.append("anchor")
            i += 1
    return roles[:n]


def mask_value(color):
    """Mask painting uses the colour's lightness: black hides, white shows, grey is partial."""
    return color.lightness()


def _corners(r):
    return [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]


def _cosmetic(color, style=Qt.SolidLine):
    pen = QPen(color, 1, style)
    pen.setCosmetic(True)
    return pen


class Canvas(QWidget):
    message = Signal(str)
    shape_selected = Signal(object)  # Shape or None
    zoom_changed = Signal(float)
    color_picked = Signal(QColor)

    def __init__(self, stack, get_active_layer):
        super().__init__()
        self.stack = stack
        self.get_active_layer = get_active_layer
        self.doc = None
        self.zoom = 1.0
        self.tool = "brush"
        self.color = QColor(20, 20, 20)
        self.fill_color = QColor(80, 140, 230)
        self.fill_enabled = False
        self.stroke_width = 8
        self.tolerance = 32
        self.text_font = QFont("Helvetica", 48)
        self.edit_mask = False      # raster tools paint the active layer's mask
        self.selection = None       # QPainterPath in doc coords, or None = everything
        self.selected_shape = None
        self._checker = _checker()
        self._reset_gesture()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)

    # ---- state ----
    def _reset_gesture(self):
        self._stroke = None         # (layer, attr) being painted with brush/eraser
        self._before = None         # its image before the stroke
        self._last = None
        self._drag_start = None     # rect/ellipse/marquee start
        self._drag_cur = None
        self._lasso = None          # QPainterPath being drawn
        self._pen_pts = []          # [[anchor, out_handle], ...]
        self._pen_dragging = False
        self._edit = None           # select tool: move/scale in progress
        self._direct = None         # direct-select: point drag in progress
        self._move = None           # move tool drag in progress
        self._grad = None           # gradient tool: [start, end, (layer, attr)]
        self._hover = None

    def set_document(self, doc):
        self.doc = doc
        self.selection = None
        self.edit_mask = False
        self._select_shape(None)
        self._reset_gesture()
        self.set_zoom(self.zoom)

    def set_zoom(self, z):
        self.zoom = max(0.05, min(z, 16.0))
        self.setFixedSize(int(self.doc.width * self.zoom), int(self.doc.height * self.zoom))
        self.zoom_changed.emit(self.zoom)
        self.update()

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.ControlModifier:  # Ctrl/Cmd + wheel zooms; plain wheel scrolls
            self.set_zoom(self.zoom * (1.25 if ev.angleDelta().y() > 0 else 0.8))
            ev.accept()
        else:
            ev.ignore()

    def set_tool(self, tool):
        self.finish_pen()  # switching tools keeps an in-progress path (if it has 2+ points)
        if tool not in ("select", "direct"):
            self._select_shape(None)
        self.tool = tool
        self.setCursor(Qt.ArrowCursor if tool in ("select", "direct") else
                       Qt.IBeamCursor if tool == "text" else
                       Qt.SizeAllCursor if tool == "move" else
                       Qt.PointingHandCursor if tool == "eyedropper" else Qt.CrossCursor)
        self.update()

    def layer_changed(self):
        self.cancel_pen()
        self._select_shape(None)

    def sync(self):
        """Call after undo/redo: follow canvas size changes and drop a selected shape that
        no longer exists."""
        if self.width() != int(self.doc.width * self.zoom) or self.height() != int(self.doc.height * self.zoom):
            self.set_zoom(self.zoom)
        layer = self.get_active_layer()
        if self.selected_shape is not None and (layer is None or layer.kind != "vector"
                                                or self.selected_shape not in layer.shapes):
            self._select_shape(None)
        self.update()

    def cancel_pen(self):
        self._pen_pts = []
        self._pen_dragging = False
        self.update()

    def _select_shape(self, shape):
        if shape is not self.selected_shape:
            self.selected_shape = shape
            self.shape_selected.emit(shape)
        self.update()

    def _doc_pos(self, ev):
        p = ev.position()
        return QPointF(p.x() / self.zoom, p.y() / self.zoom)

    def _inside(self, pt):
        return 0 <= pt.x() < self.doc.width and 0 <= pt.y() < self.doc.height

    def _vector_layer(self):
        layer = self.get_active_layer()
        if layer is None or layer.kind != "vector":
            self.message.emit("Select a vector layer to use this tool.")
            return None
        return layer

    def _raster_target(self):
        """(layer, "image") for a raster layer, (layer, "mask") when editing a mask."""
        layer = self.get_active_layer()
        if layer is not None and self.edit_mask and layer.mask is not None:
            return layer, "mask"
        if layer is not None and layer.kind == "raster":
            return layer, "image"
        self.message.emit("Select a raster layer or a layer mask to use this tool.")
        return None

    def _shape_from_path(self, path):
        fill = QColor(self.fill_color) if self.fill_enabled else None
        return Shape(path, QColor(self.color), float(self.stroke_width), fill)

    def _drag_path(self):
        path = QPainterPath()
        r = QRectF(self._drag_start, self._drag_cur).normalized()
        if self.tool == "rect":
            path.addRect(r)
        else:
            path.addEllipse(r)
        return path

    def _clip(self, p):
        if self.selection is not None:
            p.setClipPath(self.selection)

    # ---- edits callable from the window ----
    def apply_style(self, attrs):
        """Apply stroke/fill/width changes to the selected shape (one merged undo step)."""
        if self.selected_shape is not None:
            self.stack.push(SetAttrs(self.selected_shape, attrs, "Change style", merge=True))

    def apply_font(self, font):
        self.text_font = font
        s = self.selected_shape
        if s is not None and s.text:
            text = {**s.text, "font": font.toString()}
            self.stack.push(SetAttrs(s, {"text": text, "path": text_path(text)}, "Change font"))

    def delete_selected(self):
        layer = self.get_active_layer()
        if self.selected_shape is not None and layer is not None and layer.kind == "vector":
            self.stack.push(RemoveShape(layer, self.selected_shape))
            self._select_shape(None)
            return
        target = self._raster_target()
        if target is None:
            return
        obj, attr = target
        before = getattr(obj, attr)
        after = QImage(before)
        p = QPainter(after)
        self._clip(p)
        if attr == "mask":  # clearing a mask hides; Photoshop-style "fill with black"
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.fillRect(after.rect(), QColor(0, 0, 0, 0))
        else:
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(after.rect(), Qt.transparent)
        p.end()
        self.stack.push(ImageEdit(obj, before, after, "Clear", attr))

    def select_all(self):
        self.selection = QPainterPath()
        self.selection.addRect(QRectF(0, 0, self.doc.width, self.doc.height))
        self.update()

    def deselect(self):
        self.selection = None
        self.update()

    def finish_pen(self, closed=False):
        if len(self._pen_pts) >= 2:
            layer = self._vector_layer()
            if layer is not None:
                path = bezier_path(self._pen_pts, closed)
                self.stack.push(AddShape(layer, self._shape_from_path(path), "Pen path"))
        self.cancel_pen()

    def ask_text(self, initial=""):  # replaced in tests
        text, ok = QInputDialog.getText(self, "Text", "Text:", text=initial)
        return text if ok else ""

    def arrange(self, how):
        """Reorder the selected shape within its layer. Returns False if nothing selected."""
        layer, s = self.get_active_layer(), self.selected_shape
        if s is None or layer is None or layer.kind != "vector":
            return False
        i, last = layer.shapes.index(s), len(layer.shapes) - 1
        j = {"front": last, "forward": min(i + 1, last), "backward": max(i - 1, 0), "back": 0}[how]
        if j != i:
            self.stack.push(Relocate(s, layer.shapes, layer.shapes, j, "Arrange"))
        return True

    def align(self, how):
        """Align the selected shape to the canvas edges or centre."""
        s = self.selected_shape
        if s is None:
            self.message.emit("Select a shape to align.")
            return
        r, w, h = s.path.boundingRect(), self.doc.width, self.doc.height
        dx = {"left": -r.left(), "hcenter": w / 2 - r.center().x(), "right": w - r.right()}.get(how, 0)
        dy = {"top": -r.top(), "vcenter": h / 2 - r.center().y(), "bottom": h - r.bottom()}.get(how, 0)
        if dx or dy:
            self.stack.push(SetAttrs(s, s.transformed(QTransform().translate(dx, dy)), "Align"))

    # ---- raster helpers ----
    def _paint_segment(self, a, b):
        obj, attr = self._stroke
        p = QPainter(getattr(obj, attr))
        p.setRenderHint(QPainter.Antialiasing)
        self._clip(p)
        if attr == "mask":  # Source sets the mask value exactly (with antialiased edges)
            p.setCompositionMode(QPainter.CompositionMode_Source)
            color = QColor(0, 0, 0, 255 if self.tool == "eraser" else mask_value(self.color))
        else:
            if self.tool == "eraser":
                p.setCompositionMode(QPainter.CompositionMode_Clear)
            color = self.color
        p.setPen(QPen(color, self.stroke_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if a == b:
            p.drawPoint(a)
        else:
            p.drawLine(QLineF(a, b))
        p.end()

    def _bucket(self, target, pt):
        if not self._inside(pt):
            return
        obj, attr = target
        before = getattr(obj, attr)
        region = flood_region(before, int(pt.x()), int(pt.y()), self.tolerance)
        after = QImage(before)
        p = QPainter(after)
        self._clip(p)
        if attr == "mask":
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.drawImage(0, 0, region_mask(before, region, mask_value(self.color)))
        else:
            p.drawImage(0, 0, region_fill(before, region, self.color))
        p.end()
        self.stack.push(ImageEdit(obj, before, after, "Paint bucket", attr))

    def _gradient(self, start, end, target):
        if QLineF(start, end).length() < 1:
            return
        obj, attr = target
        before = getattr(obj, attr)
        after = QImage(before)
        g = QLinearGradient(start, end)
        if attr == "mask":
            g.setColorAt(0, QColor(0, 0, 0, mask_value(self.color)))
            g.setColorAt(1, QColor(0, 0, 0, mask_value(self.fill_color)))
        else:
            g.setColorAt(0, self.color)
            g.setColorAt(1, self.fill_color)
        p = QPainter(after)
        self._clip(p)
        if attr == "mask":
            p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(after.rect(), g)
        p.end()
        self.stack.push(ImageEdit(obj, before, after, "Gradient", attr))

    def _pick_color(self, pt):
        if self._inside(pt):
            c = self.doc.flatten().pixelColor(int(pt.x()), int(pt.y()))
            if c.alpha() > 0:
                c.setAlpha(255)
                self.color = c
                self.color_picked.emit(c)

    # ---- move tool ----
    def _start_move(self, pt):
        layer = self.get_active_layer()
        if layer is None:
            return
        if self.selection is not None and (layer.kind == "raster" or (self.edit_mask and layer.mask is not None)):
            # lift the selected pixels into a floating piece; the hole is cleared
            attr = "mask" if self.edit_mask and layer.mask is not None else "image"
            before = getattr(layer, attr)
            piece = QImage(before.size(), before.format())
            piece.fill(Qt.transparent)
            p = QPainter(piece)
            p.setClipPath(self.selection)
            p.drawImage(0, 0, before)
            p.end()
            base = QImage(before)
            p = QPainter(base)
            p.setClipPath(self.selection)
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(base.rect(), Qt.transparent)
            p.end()
            self._move = {"start": pt, "mode": "float", "layer": layer, "attr": attr, "before": before,
                          "base": base, "piece": piece, "sel": QPainterPath(self.selection)}
        else:  # move the whole layer (or group): pixels, mask and shapes together
            orig = []
            for l in self.doc.walk([layer]):
                if l.kind == "raster":
                    orig.append((l, {"image": l.image}))
                if l.mask is not None:
                    orig.append((l, {"mask": l.mask}))
                if l.kind == "vector":
                    orig += [(s, {"path": s.path, "text": s.text}) for s in l.shapes]
            self._move = {"start": pt, "mode": "layer", "orig": orig}

    def _move_values(self, d):
        m = self._move
        if m["mode"] == "float":
            img = QImage(m["base"])
            p = QPainter(img)
            if m["attr"] == "mask":
                p.setClipPath(m["sel"].translated(d))
                p.setCompositionMode(QPainter.CompositionMode_Source)
            p.drawImage(d, m["piece"])
            p.end()
            return [(m["layer"], {m["attr"]: img})]
        t = QTransform().translate(d.x(), d.y())
        out = []
        for obj, vals in m["orig"]:
            if "path" in vals:
                out.append((obj, Shape(vals["path"], obj.stroke, obj.width, None, vals["text"]).transformed(t)))
            else:
                out.append((obj, {k: shifted(v, d.x(), d.y()) for k, v in vals.items()}))
        return out

    def _move_delta(self, pt):
        d = pt - self._move["start"]
        return QPointF(round(d.x()), round(d.y()))  # whole pixels keep raster content sharp

    def _finish_move(self, pt):
        m, d = self._move, self._move_delta(pt)
        values = self._move_values(d) if not d.isNull() else []
        self._move = None
        if m["mode"] == "float":
            setattr(m["layer"], m["attr"], m["before"])
            self.selection = m["sel"].translated(d)
            if values:
                after = values[0][1][m["attr"]]
                self.stack.push(ImageEdit(m["layer"], m["before"], after, "Move selection", m["attr"]))
            return
        for obj, vals in m["orig"]:  # restore, then record the move as one undo step
            for k, v in vals.items():
                setattr(obj, k, v)
        if values:
            self.stack.beginMacro("Move layer")
            for obj, vals in values:
                self.stack.push(SetAttrs(obj, vals, "Move"))
            self.stack.endMacro()

    # ---- vector helpers ----
    def _hit_shape(self, layer, pt):
        tol = HIT_TOL / self.zoom
        for s in reversed(layer.shapes):  # topmost first
            stroker = QPainterPathStroker()
            stroker.setWidth(s.width + 2 * tol)
            if stroker.createStroke(s.path).contains(pt) or (s.fill is not None and s.path.contains(pt)):
                return s
        return None

    def _hit_handle(self, pt):
        if self.selected_shape is None:
            return None
        corners = _corners(self.selected_shape.path.boundingRect())
        for i, c in enumerate(corners):
            if QLineF(pt, c).length() * self.zoom <= HANDLE:
                return c, corners[(i + 2) % 4]  # (dragged corner, fixed opposite corner)
        return None

    def _hit_point(self, shape, pt):
        """Indices to drag for the path point under pt: an anchor brings its curve handles
        and any anchor at the same spot (closing points); a handle moves alone."""
        path, roles = shape.path, point_roles(shape.path)
        best, best_d = None, HANDLE / self.zoom
        for i in range(path.elementCount()):
            e = path.elementAt(i)
            d = QLineF(pt, QPointF(e.x, e.y)).length()
            if d <= best_d:
                best, best_d = i, d
        if best is None:
            return None
        if roles[best] == "control":
            return [best]
        e0 = path.elementAt(best)
        idx = set()
        for i, role in enumerate(roles):
            e = path.elementAt(i)
            if role == "anchor" and abs(e.x - e0.x) < 1e-6 and abs(e.y - e0.y) < 1e-6:
                idx.add(i)
                if i > 0 and roles[i - 1] == "control":
                    idx.add(i - 1)
                if i + 1 < len(roles) and roles[i + 1] == "control":
                    idx.add(i + 1)
        return sorted(idx)

    def _edit_transform(self, pt):
        e = self._edit
        if e["handle"] is None:  # move
            d = pt - e["start"]
            return QTransform().translate(d.x(), d.y())
        corner, fixed = e["handle"]  # scale about the opposite corner (dragging past it flips)
        sx = (pt.x() - fixed.x()) / (corner.x() - fixed.x()) if corner.x() != fixed.x() else 1
        sy = (pt.y() - fixed.y()) / (corner.y() - fixed.y()) if corner.y() != fixed.y() else 1
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:  # Shift keeps proportions
            s = max(abs(sx), abs(sy))
            sx, sy = (s if sx >= 0 else -s), (s if sy >= 0 else -s)
        return (QTransform().translate(fixed.x(), fixed.y()).scale(sx or 1e-3, sy or 1e-3)
                .translate(-fixed.x(), -fixed.y()))

    def _direct_path(self, pt):
        dr = self._direct
        d = pt - dr["start"]
        path = QPainterPath(dr["orig"])
        for i in dr["indices"]:
            e = dr["orig"].elementAt(i)
            path.setElementPositionAt(i, e.x + d.x(), e.y + d.y())
        return path

    def _edit_text(self, shape):
        new = self.ask_text(shape.text["text"])
        if new and new != shape.text["text"]:
            text = {**shape.text, "text": new}
            self.stack.push(SetAttrs(shape, {"text": text, "path": text_path(text)}, "Edit text"))

    def _new_text(self, layer, pt):
        s = self.ask_text()
        if s:
            text = {"text": s, "font": self.text_font.toString(), "x": pt.x(), "y": pt.y(),
                    "transform": QTransform()}
            shape = Shape(text_path(text), QColor(self.color), 0, QColor(self.color), text)
            self.stack.push(AddShape(layer, shape, "Text"))

    # ---- events ----
    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton or self.doc is None:
            return
        pt = self._doc_pos(ev)
        if self.tool in SELECTION_TOOLS:
            self._drag_start = self._drag_cur = pt
            if self.tool == "lasso":
                self._lasso = QPainterPath(pt)
        elif self.tool == "eyedropper":
            self._pick_color(pt)
        elif self.tool == "move":
            self._start_move(pt)
        elif self.tool in RASTER_TOOLS:
            target = self._raster_target()
            if target is None:
                return
            if self.tool == "bucket":
                self._bucket(target, pt)
            elif self.tool == "gradient":
                self._grad = [pt, pt, target]
            else:
                self._stroke = target
                self._before = QImage(getattr(*target))  # new handle; painting detaches
                self._last = pt
                self._paint_segment(pt, pt)
        else:
            layer = self._vector_layer()
            if layer is not None:
                self._press_vector(layer, pt)
        self.update()

    def _press_vector(self, layer, pt):
        if self.tool == "select":
            handle = self._hit_handle(pt)
            if handle is None:
                self._select_shape(self._hit_shape(layer, pt))
            s = self.selected_shape
            if s is not None:
                self._edit = {"start": pt, "orig": QPainterPath(s.path), "text": s.text, "handle": handle}
        elif self.tool == "direct":
            s = self.selected_shape
            indices = self._hit_point(s, pt) if s is not None else None
            if indices:
                self._direct = {"start": pt, "orig": QPainterPath(s.path), "indices": indices}
            else:
                self._select_shape(self._hit_shape(layer, pt))
        elif self.tool in ("rect", "ellipse"):
            self._drag_start = self._drag_cur = pt
        elif self.tool == "pen":
            if len(self._pen_pts) >= 3 and QLineF(pt, self._pen_pts[0][0]).length() * self.zoom <= CLOSE_RADIUS:
                self.finish_pen(closed=True)
                return
            self._pen_pts.append([pt, None])
            self._pen_dragging = True
        elif self.tool == "text":
            hit = self._hit_shape(layer, pt)
            if hit is not None and hit.text:
                self._edit_text(hit)
            else:
                self._new_text(layer, pt)

    def mouseMoveEvent(self, ev):
        pt = self._doc_pos(ev)
        self._hover = pt
        if self._stroke is not None:
            self._paint_segment(self._last, pt)
            self._last = pt
        elif self._move is not None:
            for obj, vals in self._move_values(self._move_delta(pt)):
                for k, v in vals.items():
                    setattr(obj, k, v)
            if self._move["mode"] == "float":
                self.selection = self._move["sel"].translated(self._move_delta(pt))
        elif self._edit is not None:
            self.selected_shape.path = self._edit_transform(pt).map(self._edit["orig"])
        elif self._direct is not None:
            self.selected_shape.path = self._direct_path(pt)
        elif self._grad is not None:
            self._grad[1] = pt
        elif self._lasso is not None:
            self._lasso.lineTo(pt)
        elif self._drag_start is not None:
            self._drag_cur = pt
        elif self._pen_dragging and self._pen_pts:
            if QLineF(pt, self._pen_pts[-1][0]).length() * self.zoom > 2:
                self._pen_pts[-1][1] = pt  # dragging out a curve handle
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        pt = self._doc_pos(ev)
        if self._stroke is not None:
            obj, attr = self._stroke
            self.stack.push(ImageEdit(obj, self._before, QImage(getattr(obj, attr)),
                                      "Erase" if self.tool == "eraser" else "Brush stroke", attr))
            self._stroke = self._before = None
        elif self._move is not None:
            self._finish_move(pt)
        elif self._edit is not None:
            shape, e = self.selected_shape, self._edit
            t = self._edit_transform(pt)
            shape.path = e["orig"]  # SetAttrs records the pre-edit values for undo
            if not t.isIdentity():
                new = Shape(e["orig"], shape.stroke, shape.width, None, e["text"]).transformed(t)
                self.stack.push(SetAttrs(shape, new, "Move shape" if e["handle"] is None else "Resize shape"))
            self._edit = None
        elif self._direct is not None:
            shape, new = self.selected_shape, self._direct_path(pt)
            shape.path = self._direct["orig"]
            if new != shape.path:  # hand-edited text becomes a plain path
                self.stack.push(SetAttrs(shape, {"path": new, "text": None}, "Edit points"))
            self._direct = None
        elif self._grad is not None:
            start, _, target = self._grad
            self._grad = None
            self._gradient(start, pt, target)
        elif self.tool in SELECTION_TOOLS and self._drag_start is not None:
            if self._lasso is not None:
                self._lasso.closeSubpath()
                path = self._lasso
            else:
                path = QPainterPath()
                path.addRect(QRectF(self._drag_start, pt).normalized())
            r = path.boundingRect()
            # a click without a drag clears the selection
            self.selection = path if r.width() * self.zoom > 2 and r.height() * self.zoom > 2 else None
            self._drag_start = self._drag_cur = self._lasso = None
        elif self._drag_start is not None:
            self._drag_cur = pt
            r = QRectF(self._drag_start, self._drag_cur).normalized()
            if r.width() >= 1 and r.height() >= 1:
                layer = self._vector_layer()
                if layer is not None:
                    self.stack.push(AddShape(layer, self._shape_from_path(self._drag_path()),
                                             "Rectangle" if self.tool == "rect" else "Ellipse"))
            self._drag_start = self._drag_cur = None
        self._pen_dragging = False
        self.update()

    def mouseDoubleClickEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        if self.tool == "pen":
            self.finish_pen(closed=False)
        elif self.tool == "select" and self.selected_shape is not None and self.selected_shape.text:
            self._edit_text(self.selected_shape)

    def keyPressEvent(self, ev):
        if self.tool == "pen" and ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish_pen(closed=False)
        elif ev.key() == Qt.Key_Escape:
            self.cancel_pen()
            self._select_shape(None)
        else:
            super().keyPressEvent(ev)

    # ---- drawing ----
    def paintEvent(self, ev):
        if self.doc is None:
            return
        p = QPainter(self)
        p.fillRect(self.rect(), self._checker)
        p.scale(self.zoom, self.zoom)
        if self.zoom < 1:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
        # composite off-screen first so blend modes mix layers, not the checkerboard
        p.drawImage(0, 0, self.doc.flatten())
        p.setRenderHint(QPainter.Antialiasing)

        if self._drag_start is not None and self.tool in ("rect", "ellipse"):
            self._shape_from_path(self._drag_path()).paint(p)
        if self._pen_pts:
            pts = self._pen_pts + ([[self._hover, None]] if self._hover is not None and not self._pen_dragging else [])
            if len(pts) >= 2:
                Shape(bezier_path(pts, False), QColor(self.color), float(self.stroke_width)).paint(p)
            self._draw_pen_handles(p)
        if self._grad is not None:
            p.setPen(_cosmetic(Qt.white))
            p.drawLine(QLineF(self._grad[0], self._grad[1]))
            p.setPen(_cosmetic(Qt.black, Qt.DashLine))
            p.drawLine(QLineF(self._grad[0], self._grad[1]))
        if self.selected_shape is not None:
            if self.tool == "direct":
                self._draw_points(p, self.selected_shape.path)
            else:
                r = self.selected_shape.path.boundingRect()
                self._ants(p, None, rect=r)
                p.setPen(_cosmetic(BLUE))
                p.setBrush(Qt.white)
                h = HANDLE / self.zoom
                for c in _corners(r):
                    p.drawRect(QRectF(c.x() - h / 2, c.y() - h / 2, h, h))
        sel = self.selection
        if self._lasso is not None:
            sel = self._lasso
        elif self._drag_start is not None and self.tool == "marquee":
            sel = QPainterPath()
            sel.addRect(QRectF(self._drag_start, self._drag_cur).normalized())
        if sel is not None:
            self._ants(p, sel)
        p.end()

    def _ants(self, p, path, rect=None):
        """Dashed black-on-white outline that stays 1px wide at any zoom."""
        for color, style in ((Qt.white, Qt.SolidLine), (Qt.black, Qt.DashLine)):
            p.setPen(_cosmetic(color, style))
            p.setBrush(Qt.NoBrush)
            if rect is not None:
                p.drawRect(rect)
            else:
                p.drawPath(path)

    def _draw_points(self, p, path):
        """Direct-select view: anchors as squares, curve handles as dots on stalks."""
        roles = point_roles(path)
        p.setPen(_cosmetic(BLUE))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        h, r = HANDLE / self.zoom, 3 / self.zoom
        for i, role in enumerate(roles):
            e = path.elementAt(i)
            pt = QPointF(e.x, e.y)
            if role == "control":
                # stalk from the handle to the anchor it belongs to
                anchor = i - 1 if path.elementAt(i).type == QPainterPath.CurveToElement else i + 1
                a = path.elementAt(anchor)
                p.drawLine(QLineF(pt, QPointF(a.x, a.y)))
                p.setBrush(BLUE)
                p.drawEllipse(pt, r, r)
            else:
                p.setBrush(Qt.white)
                p.drawRect(QRectF(pt.x() - h / 2, pt.y() - h / 2, h, h))

    def _draw_pen_handles(self, p):
        p.setPen(_cosmetic(BLUE))
        r = 3 / self.zoom
        for anchor, h in self._pen_pts:
            if h is not None:
                mirror = anchor * 2 - h
                p.drawLine(QLineF(mirror, h))
                p.setBrush(BLUE)
                p.drawEllipse(h, r, r)
                p.drawEllipse(mirror, r, r)
            p.setBrush(Qt.white)
            p.drawEllipse(anchor, r, r)

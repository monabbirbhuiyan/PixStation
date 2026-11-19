"""Canvas widget: draws the document and turns mouse input into tool actions."""
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath, QPainterPathStroker,
                           QPen, QPixmap, QTransform)
from PySide6.QtWidgets import QApplication, QInputDialog, QWidget

from .document import AddShape, ImageEdit, RemoveShape, SetAttrs, Shape

RASTER_TOOLS = {"brush", "eraser"}
VECTOR_TOOLS = {"select", "rect", "ellipse", "pen", "text"}
SELECTION_TOOLS = {"marquee", "lasso"}  # pixel selections; work on any layer
CLOSE_RADIUS = 8   # screen px: clicking this close to the first pen point closes the path
HANDLE = 7         # screen px: size of shape resize handles
HIT_TOL = 4        # screen px: extra slack when clicking thin shapes


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


def _corners(r):
    return [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]


class Canvas(QWidget):
    message = Signal(str)
    shape_selected = Signal(object)  # Shape or None

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
        self.text_font = QFont("Helvetica", 48)
        self.selection = None       # QPainterPath in doc coords, or None = everything
        self.selected_shape = None
        self._checker = _checker()
        self._reset_gesture()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)

    # ---- state ----
    def _reset_gesture(self):
        self._stroke_layer = None   # raster layer being painted
        self._before = None         # its image before the stroke
        self._last = None
        self._drag_start = None     # rect/ellipse/marquee start
        self._drag_cur = None
        self._lasso = None          # QPainterPath being drawn
        self._pen_pts = []          # [[anchor, out_handle], ...]
        self._pen_dragging = False
        self._edit = None           # select tool: move/scale in progress
        self._hover = None

    def set_document(self, doc):
        self.doc = doc
        self.selection = None
        self._select_shape(None)
        self._reset_gesture()
        self.set_zoom(self.zoom)

    def set_zoom(self, z):
        self.zoom = max(0.1, min(z, 16.0))
        self.setFixedSize(int(self.doc.width * self.zoom), int(self.doc.height * self.zoom))
        self.update()

    def set_tool(self, tool):
        self.finish_pen()  # switching tools keeps an in-progress path (if it has 2+ points)
        if tool != "select":
            self._select_shape(None)
        self.tool = tool
        self.setCursor(Qt.ArrowCursor if tool == "select" else
                       Qt.IBeamCursor if tool == "text" else Qt.CrossCursor)

    def layer_changed(self):
        self.cancel_pen()
        self._select_shape(None)

    def sync(self):
        """Call after undo/redo: drop a selected shape that no longer exists."""
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

    def _layer_for_tool(self):
        layer = self.get_active_layer()
        want = "raster" if self.tool in RASTER_TOOLS else "vector"
        if layer is None or layer.kind != want:
            self.message.emit(f"Select a {want} layer to use this tool.")
            return None
        return layer

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

    # ---- edits callable from the window ----
    def apply_style(self, attrs):
        """Apply stroke/fill/width changes to the selected shape (one merged undo step)."""
        if self.selected_shape is not None:
            self.stack.push(SetAttrs(self.selected_shape, attrs, "Change style", merge=True))

    def delete_selected(self):
        layer = self.get_active_layer()
        if self.selected_shape is not None and layer is not None and layer.kind == "vector":
            self.stack.push(RemoveShape(layer, self.selected_shape))
            self._select_shape(None)
        elif layer is not None and layer.kind == "raster":
            before = QImage(layer.image)
            p = QPainter(layer.image)
            if self.selection is not None:
                p.setClipPath(self.selection)
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(layer.image.rect(), Qt.transparent)
            p.end()
            self.stack.push(ImageEdit(layer, before, QImage(layer.image), "Clear"))

    def select_all(self):
        self.selection = QPainterPath()
        self.selection.addRect(QRectF(0, 0, self.doc.width, self.doc.height))
        self.update()

    def deselect(self):
        self.selection = None
        self.update()

    def finish_pen(self, closed=False):
        if len(self._pen_pts) >= 2:
            layer = self._layer_for_tool()
            if layer is not None:
                path = bezier_path(self._pen_pts, closed)
                self.stack.push(AddShape(layer, self._shape_from_path(path), "Pen path"))
        self.cancel_pen()

    def ask_text(self):  # replaced in tests
        text, ok = QInputDialog.getText(self, "Text", "Text:")
        return text if ok else ""

    # ---- helpers ----
    def _paint_segment(self, a, b):
        p = QPainter(self._stroke_layer.image)
        p.setRenderHint(QPainter.Antialiasing)
        if self.selection is not None:
            p.setClipPath(self.selection)
        if self.tool == "eraser":
            p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.setPen(QPen(self.color, self.stroke_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if a == b:
            p.drawPoint(a)
        else:
            p.drawLine(QLineF(a, b))
        p.end()

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

    # ---- events ----
    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton or self.doc is None:
            return
        pt = self._doc_pos(ev)
        if self.tool in SELECTION_TOOLS:
            self._drag_start = self._drag_cur = pt
            if self.tool == "lasso":
                self._lasso = QPainterPath(pt)
            self.update()
            return
        layer = self._layer_for_tool()
        if layer is None:
            return
        if self.tool in RASTER_TOOLS:
            self._stroke_layer = layer
            self._before = QImage(layer.image)  # shared copy; painting detaches
            self._last = pt
            self._paint_segment(pt, pt)
        elif self.tool == "select":
            handle = self._hit_handle(pt)
            if handle is None:
                self._select_shape(self._hit_shape(layer, pt))
            if self.selected_shape is not None:
                self._edit = {"start": pt, "orig": QPainterPath(self.selected_shape.path),
                              "handle": handle}
        elif self.tool in ("rect", "ellipse"):
            self._drag_start = self._drag_cur = pt
        elif self.tool == "pen":
            if len(self._pen_pts) >= 3 and QLineF(pt, self._pen_pts[0][0]).length() * self.zoom <= CLOSE_RADIUS:
                self.finish_pen(closed=True)
                return
            self._pen_pts.append([pt, None])
            self._pen_dragging = True
        elif self.tool == "text":
            text = self.ask_text()
            if text:
                path = QPainterPath()
                path.addText(pt, self.text_font, text)
                self.stack.push(AddShape(layer, Shape(path, QColor(self.color), 0, QColor(self.color)), "Text"))
        self.update()

    def mouseMoveEvent(self, ev):
        pt = self._doc_pos(ev)
        self._hover = pt
        if self._stroke_layer is not None:
            self._paint_segment(self._last, pt)
            self._last = pt
        elif self._edit is not None:
            self.selected_shape.path = self._edited_path(pt)
        elif self._lasso is not None:
            self._lasso.lineTo(pt)
        elif self._drag_start is not None:
            self._drag_cur = pt
        elif self._pen_dragging and self._pen_pts:
            if QLineF(pt, self._pen_pts[-1][0]).length() * self.zoom > 2:
                self._pen_pts[-1][1] = pt  # dragging out a curve handle
        self.update()

    def _edited_path(self, pt):
        e = self._edit
        if e["handle"] is None:  # move
            d = pt - e["start"]
            return e["orig"].translated(d.x(), d.y())
        corner, fixed = e["handle"]  # scale about the opposite corner (dragging past it flips)
        sx = (pt.x() - fixed.x()) / (corner.x() - fixed.x()) if corner.x() != fixed.x() else 1
        sy = (pt.y() - fixed.y()) / (corner.y() - fixed.y()) if corner.y() != fixed.y() else 1
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:  # Shift keeps proportions
            s = max(abs(sx), abs(sy))
            sx, sy = (s if sx >= 0 else -s), (s if sy >= 0 else -s)
        t =QTransform().translate(fixed.x(), fixed.y()).scale(sx or 1e-3, sy or 1e-3).translate(-fixed.x(), -fixed.y())
        return t.map(e["orig"])

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        pt = self._doc_pos(ev)
        if self._stroke_layer is not None:
            layer = self._stroke_layer
            self.stack.push(ImageEdit(layer, self._before, QImage(layer.image),
                                      "Erase" if self.tool == "eraser" else "Brush stroke"))
            self._stroke_layer = self._before = None
        elif self._edit is not None:
            shape, new = self.selected_shape, self._edited_path(pt)
            shape.path = self._edit["orig"]  # SetAttrs records the pre-edit path for undo
            if new != shape.path:
                self.stack.push(SetAttrs(shape, {"path": new},
                                         "Move shape" if self._edit["handle"] is None else "Resize shape"))
            self._edit = None
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
                layer = self._layer_for_tool()
                if layer is not None:
                    self.stack.push(AddShape(layer, self._shape_from_path(self._drag_path()),
                                             "Rectangle" if self.tool == "rect" else "Ellipse"))
            self._drag_start = self._drag_cur = None
        self._pen_dragging = False
        self.update()

    def mouseDoubleClickEvent(self, ev):
        if self.tool == "pen" and ev.button() == Qt.LeftButton:
            self.finish_pen(closed=False)

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
        if self.selected_shape is not None:
            r = self.selected_shape.path.boundingRect()
            self._ants(p, None, rect=r)
            pen = QPen(QColor(0, 120, 255), 1)
            pen.setCosmetic(True)
            p.setPen(pen)
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
            pen = QPen(color, 1, style)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            if rect is not None:
                p.drawRect(rect)
            else:
                p.drawPath(path)

    def _draw_pen_handles(self, p):
        pen = QPen(QColor(0, 120, 255), 1)
        pen.setCosmetic(True)
        p.setPen(pen)
        r = 3 / self.zoom
        for anchor, h in self._pen_pts:
            if h is not None:
                mirror = anchor * 2 - h
                p.drawLine(QLineF(mirror, h))
                p.setBrush(QColor(0, 120, 255))
                p.drawEllipse(h, r, r)
                p.drawEllipse(mirror, r, r)
            p.setBrush(Qt.white)
            p.drawEllipse(anchor, r, r)

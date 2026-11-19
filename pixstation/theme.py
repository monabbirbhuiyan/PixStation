"""PixStation's dark theme and its icon set, drawn in code (no image files to ship)."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette,
                           QPen, QPixmap, QPolygonF)

BG = "#2b2b2f"        # window / panels
BASE = "#1f1f22"      # inputs, lists
CANVAS_BG = "#1a1a1c"  # area around the document
BORDER = "#3c3c42"
TEXT = "#d4d4d8"
DIM = "#8a8a92"
ACCENT = "#4a8af4"

STYLE = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QToolBar {{ background: {BG}; border: none; spacing: 2px; padding: 3px; }}
QToolBar#tools {{ border-right: 1px solid {BORDER}; }}
QToolBar#options {{ border-bottom: 1px solid {BORDER}; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; height: 1px; margin: 4px; }}
QToolButton {{ border: 1px solid transparent; border-radius: 4px; padding: 3px; color: {TEXT}; }}
QToolButton:hover {{ background: #38383e; }}
QToolButton:checked {{ background: #44444c; border-color: #55555e; }}
QDockWidget {{ color: {TEXT}; titlebar-close-icon: none; }}
QDockWidget::title {{ background: #252528; padding: 5px 8px; border-bottom: 1px solid {BORDER}; }}
QListView::item {{ padding: 3px 4px; }}
QListWidget, QListView, QUndoView, QTreeWidget {{ background: {BASE}; border: 1px solid {BORDER}; outline: 0; }}
QTreeWidget::item {{ padding: 4px 2px; border-bottom: 1px solid #29292d; }}
QListWidget::item:selected, QListView::item:selected, QTreeWidget::item:selected {{ background: #34446a; color: white; }}
QScrollArea#canvasArea {{ background: {CANVAS_BG}; border: none; }}
QScrollArea#canvasArea > QWidget > QWidget {{ background: {CANVAS_BG}; }}
QStatusBar {{ background: #252528; color: {DIM}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ color: {DIM}; padding: 0 8px; }}
QSpinBox, QComboBox {{ background: {BASE}; border: 1px solid {BORDER}; border-radius: 3px;
                       padding: 2px 6px; color: {TEXT}; min-height: 18px; }}
QSlider::groove:horizontal {{ height: 4px; background: #45454c; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 12px; margin: -5px 0; border-radius: 6px; }}
QPushButton {{ background: #3a3a40; border: 1px solid {BORDER}; border-radius: 4px; padding: 4px 10px; color: {TEXT}; }}
QPushButton:hover {{ background: #45454c; }}
QMenuBar {{ background: {BG}; color: {TEXT}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu {{ background: #2f2f34; color: {TEXT}; border: 1px solid {BORDER}; }}
QLabel {{ color: {TEXT}; }}
"""


def apply(app):
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in [(QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, BASE),
                        (QPalette.AlternateBase, BG), (QPalette.Text, TEXT), (QPalette.Button, "#3a3a40"),
                        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT),
                        (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, "#3a3a40"),
                        (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, DIM)]:
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(DIM))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(DIM))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)


# ---- icons: each draws into a 24×24 box ----

def _make(draw, size=24):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24, size / 24)
    pen = QPen(QColor(TEXT), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    draw(p)
    p.end()
    return QIcon(pm)


def _poly(*pts):
    return QPolygonF([QPointF(x, y) for x, y in pts])


def _select(p):
    p.setBrush(QColor(TEXT))
    p.drawPolygon(_poly((7, 3), (7, 19), (11, 15.5), (14, 21), (16.5, 20), (13.5, 14.5), (19, 14.5)))


def _marquee(p):
    pen = p.pen()
    pen.setStyle(Qt.DashLine)
    pen.setDashPattern([2, 2])
    p.setPen(pen)
    p.drawRect(QRectF(4, 5, 16, 14))


def _lasso(p):
    p.drawEllipse(QRectF(3.5, 4, 17, 11))
    path = QPainterPath(QPointF(8, 14.2))
    path.cubicTo(5, 17, 9, 19, 7, 21.5)
    p.drawPath(path)


def _brush(p):
    p.setPen(QPen(QColor(TEXT), 2.4, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(20, 4), QPointF(12.5, 11.5))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(TEXT))
    tip = QPainterPath(QPointF(11.5, 11))
    tip.cubicTo(7, 11, 6.5, 16, 4, 20.5)
    tip.cubicTo(9, 20.5, 14, 18, 13.5, 13)
    tip.closeSubpath()
    p.drawPath(tip)


def _eraser(p):
    p.drawPolygon(_poly((3.5, 15), (12, 6.5), (20, 14.5), (13, 21.5), (8, 21.5)))
    p.setBrush(QColor(TEXT))
    p.drawPolygon(_poly((3.5, 15), (8, 10.5), (16, 18.5), (13, 21.5), (8, 21.5)))


def _rect(p):
    p.drawRect(QRectF(4, 6, 16, 12))


def _ellipse(p):
    p.drawEllipse(QRectF(3.5, 5, 17, 14))


def _pen(p):
    nib = QPainterPath(QPointF(12, 2.5))
    nib.lineTo(18.5, 12)
    nib.lineTo(12, 21.5)
    nib.lineTo(5.5, 12)
    nib.closeSubpath()
    p.drawPath(nib)
    p.drawLine(QPointF(12, 2.5), QPointF(12, 10.5))
    p.setBrush(QColor(TEXT))
    p.drawEllipse(QPointF(12, 12.5), 1.6, 1.6)


def _text(p):
    f = QFont("Georgia")
    f.setPixelSize(19)
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, 0, 24, 24), Qt.AlignCenter, "T")


def _plus_badge(p):
    p.setPen(QPen(QColor(ACCENT), 2, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(18, 13), QPointF(18, 21))
    p.drawLine(QPointF(14, 17), QPointF(22, 17))


def _new_raster(p):
    p.drawRect(QRectF(3, 3, 13, 13))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(TEXT))
    p.drawRect(QRectF(3, 3, 6.5, 6.5))      # checker squares = pixels
    p.drawRect(QRectF(9.5, 9.5, 6.5, 6.5))
    _plus_badge(p)


def _new_vector(p):
    path = QPainterPath(QPointF(3, 15))
    path.cubicTo(5, 3, 13, 3, 15, 9)
    p.drawPath(path)
    p.setBrush(QColor(BG))
    p.drawRect(QRectF(1.5, 13.5, 3, 3))
    p.drawRect(QRectF(13.5, 7.5, 3, 3))
    _plus_badge(p)


def _up(p):
    p.drawPolyline(_poly((6, 14), (12, 8), (18, 14)))


def _down(p):
    p.drawPolyline(_poly((6, 10), (12, 16), (18, 10)))


def _trash(p):
    p.drawLine(QPointF(4.5, 6.5), QPointF(19.5, 6.5))
    p.drawPolyline(_poly((9.5, 6.5), (9.5, 4), (14.5, 4), (14.5, 6.5)))
    p.drawPolygon(_poly((6.5, 6.5), (7.5, 20.5), (16.5, 20.5), (17.5, 6.5)))
    p.drawLine(QPointF(10.5, 10), QPointF(10.5, 17))
    p.drawLine(QPointF(13.5, 10), QPointF(13.5, 17))


def _direct(p):
    p.setBrush(QColor(BG))
    p.drawPolygon(_poly((7, 3), (7, 19), (11, 15.5), (14, 21), (16.5, 20), (13.5, 14.5), (19, 14.5)))


def _move(p):
    p.drawLine(QPointF(12, 3), QPointF(12, 21))
    p.drawLine(QPointF(3, 12), QPointF(21, 12))
    for a, b, c in (((9, 6), (12, 3), (15, 6)), ((9, 18), (12, 21), (15, 18)),
                    ((6, 9), (3, 12), (6, 15)), ((18, 9), (21, 12), (18, 15))):
        p.drawPolyline(_poly(a, b, c))


def _eyedropper(p):
    p.drawLine(QPointF(4, 20), QPointF(13, 11))
    p.drawLine(QPointF(4, 20), QPointF(5.5, 17))
    p.setBrush(QColor(TEXT))
    body = QPainterPath(QPointF(11, 9))
    body.lineTo(15, 13)
    body.lineTo(20.5, 7.5)
    body.cubicTo(22, 6, 18, 2, 16.5, 3.5)
    body.closeSubpath()
    p.drawPath(body)


def _bucket(p):
    p.drawPolygon(_poly((4, 11), (11, 4), (18, 11), (11, 18)))
    p.drawLine(QPointF(11, 4), QPointF(8, 1.5))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(ACCENT))
    drop = QPainterPath(QPointF(19.5, 13))
    drop.cubicTo(17, 17, 17, 20, 19.5, 20)
    drop.cubicTo(22, 20, 22, 17, 19.5, 13)
    p.drawPath(drop)


def _gradient(p):
    g =QLinearGradient(4, 0, 20, 0)
    g.setColorAt(0, QColor(TEXT))
    g.setColorAt(1, QColor(BG))
    p.setBrush(g)
    p.drawRect(QRectF(4, 6, 16, 12))


def _folder(p):
    path = QPainterPath(QPointF(3, 7))
    path.lineTo(3, 18.5)
    path.lineTo(21, 18.5)
    path.lineTo(21, 8.5)
    path.lineTo(11.5, 8.5)
    path.lineTo(9.5, 5.5)
    path.lineTo(3, 5.5)
    path.closeSubpath()
    p.drawPath(path)


def _new_group(p):
    p.scale(0.8, 0.8)
    _folder(p)
    p.scale(1.25, 1.25)
    _plus_badge(p)


def _mask(p):
    p.drawRect(QRectF(3.5, 4.5, 17, 15))
    p.setBrush(QColor(TEXT))
    p.drawEllipse(QPointF(12, 12), 4.5, 4.5)


def _eye(p):
    path = QPainterPath(QPointF(2.5, 12))
    path.cubicTo(7, 5, 17, 5, 21.5, 12)
    path.cubicTo(17, 19, 7, 19, 2.5, 12)
    p.drawPath(path)
    p.setBrush(QColor(TEXT))
    p.drawEllipse(QPointF(12, 12), 3, 3)


def _eye_off(p):
    p.setPen(QPen(QColor(DIM), 1.6, Qt.SolidLine, Qt.RoundCap))
    path = QPainterPath(QPointF(2.5, 12))
    path.cubicTo(7, 17.5, 17, 17.5, 21.5, 12)  # closed eyelid
    p.drawPath(path)


ICONS = {"select": _select, "direct": _direct, "move": _move, "marquee": _marquee,
         "lasso": _lasso, "eyedropper": _eyedropper, "brush": _brush, "eraser": _eraser,
         "bucket": _bucket, "gradient": _gradient, "rect": _rect, "ellipse": _ellipse,
         "pen": _pen, "text": _text, "new_raster": _new_raster, "new_vector": _new_vector,
         "new_group": _new_group, "folder": _folder, "mask": _mask, "eye": _eye,
         "eye_off": _eye_off, "up": _up, "down": _down, "trash": _trash}


def icon(name, size=24):
    return _make(ICONS[name], size)


def swatch(color, size=18):
    """Square colour chip with a thin border (checkerboard shows through transparent colours)."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    r = QRectF(0.5, 0.5, size - 1, size - 1)
    p.fillRect(r, QColor("#ffffff"))
    p.fillRect(QRectF(0.5, 0.5, size / 2, size / 2), QColor("#c8c8c8"))
    p.fillRect(QRectF(size / 2, size / 2, size / 2 - 0.5, size / 2 - 0.5), QColor("#c8c8c8"))
    p.fillRect(r, QColor(color))
    p.setPen(QPen(QColor("#77777f"), 1))
    p.drawRect(r)
    p.end()
    return QIcon(pm)

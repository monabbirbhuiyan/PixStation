"""Main window: menus, tool bar, layer panel."""
import os

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import (QAction, QActionGroup, QColor, QIcon, QImage, QKeySequence, QPainter,
                           QPixmap, QUndoStack)
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDockWidget, QFileDialog,
                               QFontDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QScrollArea, QSlider,
                               QSpinBox, QToolBar, QToolButton, QUndoView, QVBoxLayout, QWidget)

from . import theme
from .canvas import Canvas
from .document import (BLEND_MODES, AddLayer, Document, ImageEdit, MoveLayer, RasterLayer,
                       RemoveLayer, SetAttrs, VectorLayer)
from .filters import FILTERS, FilterDialog, apply_filter
from .ora import load_ora, save_ora

TOOLS = [("select", "Select", "V"), ("marquee", "Marquee", "M"), ("lasso", "Lasso", "L"),
         None,
         ("brush", "Brush", "B"), ("eraser", "Eraser", "E"),
         None,
         ("rect", "Rectangle", "R"), ("ellipse", "Ellipse", "O"), ("pen", "Pen", "P"),
         ("text", "Text", "T")]
THUMB_W, THUMB_H = 44, 30  # layer thumbnail size in the Layers panel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.stack = QUndoStack(self)
        self.stack.setUndoLimit(100)
        self.doc = None
        self.path = None
        self.active = None
        self._layer_counter = 1

        self.canvas = Canvas(self.stack, lambda: self.active)
        self.canvas.message.connect(lambda m: self.statusBar().showMessage(m, 3000))
        self.canvas.shape_selected.connect(self._on_shape_selected)
        self.canvas.zoom_changed.connect(self._update_status)
        scroll = QScrollArea(objectName="canvasArea")
        scroll.setWidget(self.canvas)
        scroll.setAlignment(Qt.AlignCenter)
        self.setCentralWidget(scroll)
        self.zoom_label = QLabel()
        self.size_label = QLabel()
        self.statusBar().addPermanentWidget(self.size_label)
        self.statusBar().addPermanentWidget(self.zoom_label)

        self._build_layer_panel()
        self._build_toolbar()
        self._build_menus()

        self.stack.indexChanged.connect(self._on_doc_changed)
        self.stack.cleanChanged.connect(self._on_clean_changed)
        self.set_document(Document.new_default())
        self.resize(1400, 900)

    # ---- UI construction ----
    def _build_toolbar(self):
        # left: vertical tool strip, icons only
        tools = QToolBar("Tools", objectName="tools", movable=False)
        tools.setOrientation(Qt.Vertical)
        tools.setIconSize(QSize(22, 22))
        self.addToolBar(Qt.LeftToolBarArea, tools)
        group = QActionGroup(self)
        self.tool_actions = {}
        for tool in TOOLS:
            if tool is None:
                tools.addSeparator()
                continue
            key, label, shortcut = tool
            act = QAction(theme.icon(key), label, self, checkable=True, shortcut=shortcut)
            act.setToolTip(f"{label} ({shortcut})")
            act.triggered.connect(lambda _=False, k=key, l=label: self._choose_tool(k, l))
            group.addAction(act)
            tools.addAction(act)
            self.tool_actions[key] = act
        self.tool_actions["brush"].setChecked(True)

        # top: options bar for the current tool. The style controls double as the
        # style of the selected shape (Select tool).
        opts = QToolBar("Options", objectName="options", movable=False)
        self.addToolBar(Qt.TopToolBarArea, opts)
        self.tool_label = QLabel("Brush")
        self.tool_label.setStyleSheet("font-weight: 600; min-width: 72px; padding-left: 6px;")
        self.color_btn = QToolButton(text="Stroke", toolButtonStyle=Qt.ToolButtonTextBesideIcon)
        self.color_btn.clicked.connect(self._pick_stroke)
        self.fill_btn = QToolButton(text="Fill", toolButtonStyle=Qt.ToolButtonTextBesideIcon)
        self.fill_btn.clicked.connect(self._pick_fill)
        self.fill_chk = QCheckBox("Fill shapes")
        self.fill_chk.toggled.connect(self._set_fill_enabled)
        self.size_spin = QSpinBox(minimum=0, maximum=200, value=self.canvas.stroke_width, suffix=" px")
        self.size_spin.setToolTip("Brush size / stroke width (0 = no outline)")
        self.size_spin.valueChanged.connect(self._set_size)
        font_btn = QToolButton(text="Font…")
        font_btn.clicked.connect(self._pick_font)
        opts.addWidget(self.tool_label)
        opts.addSeparator()
        for w in (self.color_btn, self.fill_btn, self.fill_chk):
            opts.addWidget(w)
        opts.addSeparator()
        for w in (QLabel("Size"), self.size_spin):
            opts.addWidget(w)
        opts.addSeparator()
        opts.addWidget(font_btn)
        self._swatch(self.color_btn, self.canvas.color)
        self._swatch(self.fill_btn, self.canvas.fill_color)

    def _choose_tool(self, key, label):
        self.canvas.set_tool(key)
        self.tool_label.setText(label)

    # ---- style controls (apply to new shapes and to the selected one) ----
    def _pick_stroke(self):
        c = QColorDialog.getColor(self.canvas.color, self, options=QColorDialog.ShowAlphaChannel)
        if c.isValid():
            self.canvas.color = c
            self._swatch(self.color_btn, c)
            self.canvas.apply_style({"stroke": QColor(c)})

    def _pick_fill(self):
        c = QColorDialog.getColor(self.canvas.fill_color, self, options=QColorDialog.ShowAlphaChannel)
        if c.isValid():
            self.canvas.fill_color = c
            self._swatch(self.fill_btn, c)
            if self.canvas.fill_enabled:
                self.canvas.apply_style({"fill": QColor(c)})

    def _set_fill_enabled(self, on):
        self.canvas.fill_enabled = on
        self.canvas.apply_style({"fill": QColor(self.canvas.fill_color) if on else None})

    def _set_size(self, v):
        self.canvas.stroke_width = v
        self.canvas.apply_style({"width": float(v)})

    def _pick_font(self):
        res = QFontDialog.getFont(self.canvas.text_font, self)
        ok, font = res if isinstance(res[0], bool) else res[::-1]
        if ok:
            self.canvas.text_font = font

    def _on_shape_selected(self, shape):
        """Show the selected shape's style in the toolbar without re-applying it."""
        if shape is None:
            return
        for w in (self.fill_chk, self.size_spin):
            w.blockSignals(True)
        self.canvas.color = QColor(shape.stroke)
        self._swatch(self.color_btn, shape.stroke)
        if shape.fill is not None:
            self.canvas.fill_color = QColor(shape.fill)
            self._swatch(self.fill_btn, shape.fill)
        self.canvas.fill_enabled = shape.fill is not None
        self.fill_chk.setChecked(shape.fill is not None)
        self.canvas.stroke_width = int(shape.width)
        self.size_spin.setValue(int(shape.width))
        for w in (self.fill_chk, self.size_spin):
            w.blockSignals(False)

    def _build_layer_panel(self):
        self.layer_list = QListWidget()
        self.layer_list.currentRowChanged.connect(self._on_row_changed)
        self.layer_list.itemChanged.connect(self._on_item_changed)
        self.layer_list.setIconSize(QSize(THUMB_W, THUMB_H))
        buttons = QHBoxLayout()
        buttons.addStretch()
        for name, tip, slot in [("new_raster", "New raster layer", self.add_raster_layer),
                                ("new_vector", "New vector layer", self.add_vector_layer),
                                ("up", "Move layer up", lambda: self.move_layer(1)),
                                ("down", "Move layer down", lambda: self.move_layer(-1)),
                                ("trash", "Delete layer", self.delete_layer)]:
            b = QToolButton(icon=theme.icon(name, 18), toolTip=tip)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        self.opacity = QSlider(Qt.Horizontal, minimum=0, maximum=100)
        self.opacity.valueChanged.connect(
            lambda v: self._set_layer_attr("opacity", v / 100, "Layer opacity"))
        self.blend = QComboBox()
        for key, (label, _) in BLEND_MODES.items():
            self.blend.addItem(label, key)
        self.blend.currentIndexChanged.connect(
            lambda i: self._set_layer_attr("blend", self.blend.itemData(i), "Blend mode"))
        props = QFormLayout()
        props.addRow("Opacity", self.opacity)
        props.addRow("Blend", self.blend)
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.addLayout(props)
        lay.addWidget(self.layer_list)
        lay.addLayout(buttons)
        dock = QDockWidget("Layers", self, objectName="layersDock")
        dock.setWidget(box)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        dock.setMinimumWidth(260)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        history = QUndoView(self.stack)
        history.setEmptyLabel("Open")
        hdock = QDockWidget("History", self, objectName="historyDock")
        hdock.setWidget(history)
        hdock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.RightDockWidgetArea, hdock)
        self.splitDockWidget(hdock, dock, Qt.Vertical)  # history on top, layers below
        self.resizeDocks([hdock, dock], [180, 520], Qt.Vertical)
        self.history_dock = hdock

    def _build_menus(self):
        def add(menu, text, slot, shortcut=None):
            act = menu.addAction(text)
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(shortcut)
            return act

        m = self.menuBar().addMenu("&File")
        add(m, "New", self.new_document, QKeySequence.New)
        add(m, "Open…", self.open_file, QKeySequence.Open)
        add(m, "Save", self.save, QKeySequence.Save)
        add(m, "Save As…", self.save_as, QKeySequence.SaveAs)
        m.addSeparator()
        add(m, "Export PNG…", self.export_png)
        add(m, "Export SVG…", self.export_svg, "Ctrl+Shift+E")
        m.addSeparator()
        add(m, "Quit", self.close, QKeySequence.Quit)

        m = self.menuBar().addMenu("&Edit")
        undo = self.stack.createUndoAction(self, "Undo")
        undo.setShortcut(QKeySequence.Undo)
        redo = self.stack.createRedoAction(self, "Redo")
        redo.setShortcut(QKeySequence.Redo)
        m.addAction(undo)
        m.addAction(redo)
        m.addSeparator()
        add(m, "Clear / Delete Shape", self.canvas.delete_selected, QKeySequence.Delete)
        add(m, "Select All", self.canvas.select_all, QKeySequence.SelectAll)
        add(m, "Deselect", self.canvas.deselect, "Ctrl+D")

        m = self.menuBar().addMenu("&View")
        add(m, "Zoom In", lambda: self.canvas.set_zoom(self.canvas.zoom * 1.25), QKeySequence.ZoomIn)
        add(m, "Zoom Out", lambda: self.canvas.set_zoom(self.canvas.zoom / 1.25), QKeySequence.ZoomOut)
        add(m, "Actual Size", lambda: self.canvas.set_zoom(1.0), "Ctrl+0")
        m.addSeparator()
        m.addAction(self.history_dock.toggleViewAction())

        m = self.menuBar().addMenu("&Layer")
        add(m, "New Raster Layer", self.add_raster_layer, "Ctrl+Shift+N")
        add(m, "New Vector Layer", self.add_vector_layer, "Ctrl+Shift+V")
        add(m, "Move Up", lambda: self.move_layer(1), "Ctrl+]")
        add(m, "Move Down", lambda: self.move_layer(-1), "Ctrl+[")
        add(m, "Delete Layer", self.delete_layer)

        m = self.menuBar().addMenu("Fil&ter")
        for label, fn, params in FILTERS:
            add(m, label, lambda _=False, l=label, f=fn, ps=params: self.run_filter(l, f, ps))

    def _swatch(self, btn, color):
        btn.setIcon(theme.swatch(color))

    def _thumbnail(self, layer):
        pm = QPixmap(THUMB_W * 2, THUMB_H * 2)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.transparent)
        img = self.doc.layer_image(layer).scaled(THUMB_W * 2, THUMB_H * 2, Qt.KeepAspectRatio,
                                                  Qt.SmoothTransformation)
        w, h = img.width() / 2, img.height() / 2
        x, y = (THUMB_W - w) / 2, (THUMB_H - h) / 2
        p = QPainter(pm)
        p.fillRect(int(x), int(y), int(w), int(h), QColor("#ffffff"))  # paper behind transparency
        p.drawImage(QRectF(x, y, w, h), img)
        p.setPen(QColor("#55555e"))
        p.drawRect(QRectF(x, y, w - 1, h - 1))
        p.end()
        return QIcon(pm)

    def _update_status(self, *_):
        self.zoom_label.setText(f"{self.canvas.zoom * 100:.0f}%")
        self.size_label.setText(f"{self.doc.width} × {self.doc.height} px")

    # ---- filters ----
    def run_filter(self, label, fn, params):
        layer = self.active
        if layer is None or layer.kind != "raster":
            self.statusBar().showMessage("Filters work on raster layers.", 3000)
            return
        before = QImage(layer.image)
        sel = self.canvas.selection
        if params:
            def preview(values):
                layer.image = apply_filter(before, fn, values, sel)
                self.canvas.update()

            dlg = FilterDialog(self, label, params, preview)
            preview(dlg.values())
            accepted = dlg.exec()
            layer.image = before
            self.canvas.update()
            if not accepted:
                return
            values = dlg.values()
        else:
            values = []
        after = apply_filter(before, fn, values, sel)
        self.stack.push(ImageEdit(layer, before, after, label.rstrip("…")))

    # ---- document / layers ----
    def set_document(self, doc, path=None):
        self.doc = doc
        self.path = path  # .ora file this document saves to, if any
        self.stack.clear()
        self.active = doc.layers[-1] if doc.layers else None
        self.canvas.set_document(doc)
        self._update_status()
        self._on_doc_changed()
        self.setWindowModified(False)

    def _on_doc_changed(self, *_):
        if self.active not in self.doc.layers:
            self.active = self.doc.layers[-1] if self.doc.layers else None
        self.refresh_layers()
        self.canvas.sync()

    def _set_layer_attr(self, attr, value, text):
        if self.active is not None and getattr(self.active, attr) != value:
            self.stack.push(SetAttrs(self.active, {attr: value}, text, merge=True))

    def _sync_layer_props(self):
        layer = self.active
        for w in (self.opacity, self.blend):
            w.blockSignals(True)
            w.setEnabled(layer is not None)
        if layer is not None:
            self.opacity.setValue(round(layer.opacity * 100))
            self.blend.setCurrentIndex(self.blend.findData(layer.blend))
        for w in (self.opacity, self.blend):
            w.blockSignals(False)

    def refresh_layers(self):
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        for layer in reversed(self.doc.layers):  # top layer first
            it = QListWidgetItem(layer.name)
            it.setToolTip(f"{layer.kind} layer")
            it.setIcon(self._thumbnail(layer))
            it.setData(Qt.UserRole, layer)
            it.setFlags(it.flags() | Qt.ItemIsEditable | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if layer.visible else Qt.Unchecked)
            if layer.kind == "vector":
                f = it.font()
                f.setItalic(True)  # vector layers read as italic, raster as upright
                it.setFont(f)
            self.layer_list.addItem(it)
            if layer is self.active:
                self.layer_list.setCurrentItem(it)
        self.layer_list.blockSignals(False)
        self._sync_layer_props()

    def _on_row_changed(self, row):
        if row >= 0:
            self.active = self.layer_list.item(row).data(Qt.UserRole)
            self.canvas.layer_changed()
            self._sync_layer_props()

    def _on_item_changed(self, item):
        layer = item.data(Qt.UserRole)
        layer.name = item.text()
        layer.visible = item.checkState() == Qt.Checked
        self.canvas.update()

    def _insert_index(self):
        return self.doc.layers.index(self.active) + 1 if self.active else len(self.doc.layers)

    def add_raster_layer(self):
        self._layer_counter += 1
        layer = RasterLayer(f"Raster {self._layer_counter}", self.doc.width, self.doc.height)
        self.stack.push(AddLayer(self.doc, layer, self._insert_index()))
        self.active = layer
        self.refresh_layers()

    def add_vector_layer(self):
        self._layer_counter += 1
        layer = VectorLayer(f"Vector {self._layer_counter}")
        self.stack.push(AddLayer(self.doc, layer, self._insert_index()))
        self.active = layer
        self.refresh_layers()

    def move_layer(self, delta):
        if self.active is None:
            return
        i = self.doc.layers.index(self.active)
        j = i + delta
        if 0 <= j < len(self.doc.layers):
            self.stack.push(MoveLayer(self.doc, i, j))

    def delete_layer(self):
        if self.active is not None:
            self.stack.push(RemoveLayer(self.doc, self.doc.layers.index(self.active)))

    # ---- files ----
    def _confirm_discard(self):
        if self.stack.isClean():
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes before continuing?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def new_document(self):
        if self._confirm_discard():
            self.set_document(Document.new_default())

    def open_file(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open", "", "Strata / OpenRaster or image (*.ora *.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        if path.lower().endswith(".ora"):
            try:
                self.set_document(load_ora(path), path)
            except (OSError, ValueError) as e:
                QMessageBox.warning(self, "Open failed", f"Could not open {path}:\n{e}")
            return
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Open failed", f"Could not read {path}")
            return
        self.set_document(Document.from_image(img))  # not .ora → first Save asks where

    def save(self):
        return self._write_ora(self.path) if self.path else self.save_as()

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save As", self.path or "untitled.ora",
                                              "OpenRaster (*.ora)")
        if not path:
            return False
        if not path.lower().endswith(".ora"):
            path += ".ora"
        return self._write_ora(path)

    def _write_ora(self, path):
        try:
            save_ora(self.doc, path)
        except OSError as e:
            QMessageBox.warning(self, "Save failed", f"Could not write {path}:\n{e}")
            return False
        self.path = path
        self.stack.setClean()
        self.setWindowModified(False)
        self.statusBar().showMessage(f"Saved {path}", 4000)
        return True

    def export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export PNG", "untitled.png", "PNG (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if self.doc.flatten().save(path, "PNG"):
            self.statusBar().showMessage(f"Exported {path}", 4000)
        else:
            QMessageBox.warning(self, "Export failed", f"Could not write {path}")

    def export_svg(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export SVG", "untitled.svg", "SVG (*.svg)")
        if not path:
            return
        if not path.lower().endswith(".svg"):
            path += ".svg"
        self.doc.export_svg(path)
        self.statusBar().showMessage(f"Exported {path}", 4000)

    def closeEvent(self, ev):
        if self._confirm_discard():
            ev.accept()
        else:
            ev.ignore()

    def _on_clean_changed(self, clean):  # bound method: auto-disconnected when the window dies
        self.setWindowModified(not clean)

    def setWindowModified(self, modified):
        name = os.path.basename(self.path) if self.path else "Untitled"
        self.setWindowTitle(f"{name}[*] — Strata")
        super().setWindowModified(modified)

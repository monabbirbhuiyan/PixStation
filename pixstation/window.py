"""Main window: tool strip, options bar, layers/history panels, menus, file I/O."""
import os

from PySide6.QtCore import QMimeData, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QColor, QIcon, QImage, QKeySequence, QPainter,
                           QPixmap, QUndoStack)
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox, QDockWidget, QFileDialog, QFontDialog,
                               QFormLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QScrollArea, QSlider, QSpinBox, QToolBar, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QUndoView, QVBoxLayout, QWidget)

from . import theme
from .canvas import RASTER_TOOLS, Canvas
from .document import (BLEND_MODES, AddShape, Document, GroupLayer, ImageEdit, RasterLayer,
                       Relocate, RemoveShape, SetAttrs, Splice, VectorLayer, blank_image,
                       blank_mask, mask_to_gray, ResizeCanvas)
from .filters import FILTERS, FilterDialog, apply_filter
from .ora import load_ora, save_ora

TOOLS = [("move", "Move", "Shift+V"), ("select", "Select", "V"), ("direct", "Direct Select", "A"),
         None,
         ("marquee", "Marquee", "M"), ("lasso", "Lasso", "L"),
         None,
         ("brush", "Brush", "B"), ("eraser", "Eraser", "E"), ("bucket", "Paint Bucket", "K"),
         ("gradient", "Gradient", "G"), ("eyedropper", "Eyedropper", "I"),
         None,
         ("rect", "Rectangle", "R"), ("ellipse", "Ellipse", "O"), ("pen", "Pen", "P"),
         ("text", "Text", "T")]

# Options-bar controls shown for each tool, and a one-line hint.
SHAPE_OPTS = ["color", "fill", "fillchk", "size"]
TOOL_OPTIONS = {
    "move": ([], "Drag to move the layer, or just the selected pixels if there is a selection."),
    "select": (SHAPE_OPTS + ["font"], "Click a shape. Drag to move, drag a corner to resize "
               "(Shift keeps proportions). Double-click text to edit it."),
    "direct": (SHAPE_OPTS, "Click a shape, then drag its points or curve handles."),
    "marquee": ([], "Drag to select. Click once to deselect."),
    "lasso": ([], "Drag around an area to select it."),
    "brush": (["color", "size"], "On a mask, black hides, white shows, grey is in between."),
    "eraser": (["size"], "On a mask, the eraser reveals."),
    "bucket": (["color", "tolerance"], "Fills connected pixels of similar colour."),
    "gradient": (["color", "fill"], "Drag from where the Color starts to where the To colour ends."),
    "eyedropper": ([], "Click to pick a colour from the image."),
    "rect": (SHAPE_OPTS, "Drag to draw."),
    "ellipse": (SHAPE_OPTS, "Drag to draw."),
    "pen": (SHAPE_OPTS, "Click for corners, drag for curves. Click the first point to close; "
            "double-click or Enter to finish."),
    "text": (["color", "font"], "Click to add text, or click existing text to edit it."),
}
THUMB_W, THUMB_H = 44, 30  # layer thumbnail size in the Layers panel
CLIP_SHAPES = "application/x-pixstation-shapes"
CLIP_OFFSET = "application/x-pixstation-offset"


class SizeDialog(QDialog):
    def __init__(self, parent, title, w, h, background=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        self.w = QSpinBox(minimum=1, maximum=20000, value=w, suffix=" px")
        self.h = QSpinBox(minimum=1, maximum=20000, value=h, suffix=" px")
        form.addRow("Width", self.w)
        form.addRow("Height", self.h)
        self.bg = None
        if background:
            self.bg = QComboBox()
            self.bg.addItems(["White", "Transparent"])
            form.addRow("Background", self.bg)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.stack = QUndoStack(self)
        self.stack.setUndoLimit(100)
        self.doc = None
        self.path = None
        self.active = None
        self._layer_counter = 1
        self._clip_shapes = None

        self.canvas = Canvas(self.stack, lambda: self.active)
        self.canvas.message.connect(lambda m: self.statusBar().showMessage(m, 3000))
        self.canvas.shape_selected.connect(self._on_shape_selected)
        self.canvas.zoom_changed.connect(self._update_status)
        self.canvas.color_picked.connect(lambda c: self._swatch(self.color_btn, c))
        self.scroll = QScrollArea(objectName="canvasArea")
        self.scroll.setWidget(self.canvas)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.setCentralWidget(self.scroll)
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
        self._choose_tool("brush", "Brush")
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

        # top: options for the current tool. The style controls double as the style of
        # the selected shape (Select / Direct Select).
        opts = QToolBar("Options", objectName="options", movable=False)
        self.addToolBar(Qt.TopToolBarArea, opts)
        self.tool_label = QLabel("Brush")
        self.tool_label.setStyleSheet("font-weight: 600; min-width: 96px; padding-left: 6px;")
        opts.addWidget(self.tool_label)
        opts.addSeparator()
        self.color_btn = QToolButton(text="Color", toolButtonStyle=Qt.ToolButtonTextBesideIcon)
        self.color_btn.clicked.connect(self._pick_stroke)
        self.fill_btn = QToolButton(text="Fill", toolButtonStyle=Qt.ToolButtonTextBesideIcon)
        self.fill_btn.clicked.connect(self._pick_fill)
        self.fill_chk = QCheckBox("Fill shapes")
        self.fill_chk.toggled.connect(self._set_fill_enabled)
        self.size_spin = QSpinBox(minimum=0, maximum=500, value=self.canvas.stroke_width, suffix=" px")
        self.size_spin.setToolTip("Brush size / stroke width (0 = no outline)")
        self.size_spin.valueChanged.connect(self._set_size)
        self.tol_spin = QSpinBox(minimum=0, maximum=255, value=self.canvas.tolerance)
        self.tol_spin.setToolTip("How different a colour can be and still get filled")
        self.tol_spin.valueChanged.connect(lambda v: setattr(self.canvas, "tolerance", v))
        font_btn = QToolButton(text="Font…")
        font_btn.clicked.connect(self._pick_font)
        groups = {"color": [self.color_btn], "fill": [self.fill_btn], "fillchk": [self.fill_chk],
                  "size": [QLabel(" Size "), self.size_spin],
                  "tolerance": [QLabel(" Tolerance "), self.tol_spin], "font": [font_btn]}
        self._opt_actions = {k: [opts.addWidget(w) for w in ws] for k, ws in groups.items()}
        self.hint_label = QLabel()
        self.hint_label.setStyleSheet(f"color: {theme.DIM}; padding-left: 12px;")
        opts.addWidget(self.hint_label)
        self._swatch(self.color_btn, self.canvas.color)
        self._swatch(self.fill_btn, self.canvas.fill_color)

    def _choose_tool(self, key, label):
        self.canvas.set_tool(key)
        self.tool_actions[key].setChecked(True)
        self.tool_label.setText(label)
        shown, hint = TOOL_OPTIONS[key]
        for name, actions in self._opt_actions.items():
            for a in actions:
                a.setVisible(name in shown)
        self.color_btn.setText("Color" if key in RASTER_TOOLS else "Stroke")
        self.fill_btn.setText("To" if key == "gradient" else "Fill")
        self.hint_label.setText(hint)

    def _build_layer_panel(self):
        tree = self.layer_tree = QTreeWidget()
        tree.setColumnCount(2)
        tree.setHeaderHidden(True)
        tree.setIndentation(14)
        tree.setIconSize(QSize(THUMB_W * 2 + 6, THUMB_H))
        # column 0: thumbnail + name (holds the group arrows); column 1: the eye
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0, tree.header().ResizeMode.Stretch)
        tree.header().setSectionResizeMode(1, tree.header().ResizeMode.Fixed)
        tree.setColumnWidth(1, 30)
        tree.setEditTriggers(QTreeWidget.DoubleClicked | QTreeWidget.EditKeyPressed)
        tree.currentItemChanged.connect(self._on_current_item)
        tree.itemClicked.connect(self._on_item_clicked)
        tree.itemChanged.connect(self._on_item_renamed)
        tree.itemExpanded.connect(lambda it: setattr(it.data(0, Qt.UserRole), "expanded", True))
        tree.itemCollapsed.connect(lambda it: setattr(it.data(0, Qt.UserRole), "expanded", False))

        buttons = QHBoxLayout()
        buttons.addStretch()
        for name, tip, slot in [("new_raster", "New raster layer", self.add_raster_layer),
                                ("new_vector", "New vector layer", self.add_vector_layer),
                                ("new_group", "New group", self.add_group),
                                ("mask", "Add layer mask / edit mask", self._mask_button),
                                ("up", "Move layer up", lambda: self.move_layer(1)),
                                ("down", "Move layer down", lambda: self.move_layer(-1)),
                                ("trash", "Delete layer", self.delete_layer)]:
            b = QToolButton(icon=theme.icon(name, 18), toolTip=tip)
            b.clicked.connect(slot)
            buttons.addWidget(b)
            if name == "mask":
                self.mask_btn = b
                b.setCheckable(True)
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
        lay.addWidget(tree)
        lay.addLayout(buttons)
        dock = QDockWidget("Layers", self, objectName="layersDock")
        dock.setWidget(box)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        dock.setMinimumWidth(290)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        history = QUndoView(self.stack)
        history.setEmptyLabel("Open")
        hdock = QDockWidget("History", self, objectName="historyDock")
        hdock.setWidget(history)
        hdock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.RightDockWidgetArea, hdock)
        self.splitDockWidget(hdock, dock, Qt.Vertical)  # history on top, layers below
        self.resizeDocks([hdock, dock], [170, 530], Qt.Vertical)
        self.history_dock = hdock

    def _build_menus(self):
        def add(menu, text, slot, shortcut=None):
            act = menu.addAction(text)
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(shortcut)
            return act

        m = self.menuBar().addMenu("&File")
        add(m, "New…", self.new_document, QKeySequence.New)
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
        add(m, "Cut", lambda: self.copy(cut=True), QKeySequence.Cut)
        add(m, "Copy", self.copy, QKeySequence.Copy)
        add(m, "Paste", self.paste, QKeySequence.Paste)
        add(m, "Clear / Delete Shape", self.canvas.delete_selected, QKeySequence.Delete)
        m.addSeparator()
        add(m, "Select All", self.canvas.select_all, QKeySequence.SelectAll)
        add(m, "Deselect", self.canvas.deselect, "Ctrl+D")

        m = self.menuBar().addMenu("&Image")
        add(m, "Canvas Size…", self.canvas_size)
        add(m, "Crop to Selection", self.crop_to_selection)

        m = self.menuBar().addMenu("&Layer")
        add(m, "New Raster Layer", self.add_raster_layer, "Ctrl+Shift+N")
        add(m, "New Vector Layer", self.add_vector_layer, "Ctrl+Shift+V")
        add(m, "New Group", self.add_group)
        add(m, "Duplicate Layer", self.duplicate_layer, "Ctrl+J")
        add(m, "Delete Layer", self.delete_layer)
        m.addSeparator()
        add(m, "Group Layer", self.group_layer, "Ctrl+G")
        add(m, "Ungroup", self.ungroup, "Ctrl+Shift+G")
        add(m, "Merge Down", self.merge_down, "Ctrl+E")
        m.addSeparator()
        add(m, "Add Layer Mask", self.add_mask)
        add(m, "Delete Layer Mask", self.delete_mask)
        self.edit_mask_action = add(m, "Edit Mask", lambda on: self.set_edit_mask(on), "\\")
        self.edit_mask_action.setCheckable(True)
        m.addSeparator()
        add(m, "Move Layer Up", lambda: self.move_layer(1))
        add(m, "Move Layer Down", lambda: self.move_layer(-1))

        m = self.menuBar().addMenu("&Object")
        add(m, "Bring to Front", lambda: self.canvas.arrange("front"), "Ctrl+Shift+]")
        add(m, "Bring Forward (or Move Layer Up)", lambda: self._bracket("forward"), "Ctrl+]")
        add(m, "Send Backward (or Move Layer Down)", lambda: self._bracket("backward"), "Ctrl+[")
        add(m, "Send to Back", lambda: self.canvas.arrange("back"), "Ctrl+Shift+[")
        align = m.addMenu("Align to Canvas")
        for how, label in [("left", "Left"), ("hcenter", "Horizontal Centre"), ("right", "Right"),
                           ("top", "Top"), ("vcenter", "Vertical Centre"), ("bottom", "Bottom")]:
            add(align, label, lambda _=False, h=how: self.canvas.align(h))

        m = self.menuBar().addMenu("&View")
        add(m, "Zoom In", lambda: self.canvas.set_zoom(self.canvas.zoom * 1.25), QKeySequence.ZoomIn)
        add(m, "Zoom Out", lambda: self.canvas.set_zoom(self.canvas.zoom / 1.25), QKeySequence.ZoomOut)
        add(m, "Fit on Screen", self.zoom_to_fit, "Ctrl+0")
        add(m, "Actual Size", lambda: self.canvas.set_zoom(1.0), "Ctrl+1")
        m.addSeparator()
        m.addAction(self.history_dock.toggleViewAction())

        m = self.menuBar().addMenu("Fil&ter")
        for label, fn, params in FILTERS:
            add(m, label, lambda _=False, l=label, f=fn, ps=params: self.run_filter(l, f, ps))

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
            self.canvas.apply_font(font)

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

    def _swatch(self, btn, color):
        btn.setIcon(theme.swatch(color))

    # ---- layer panel ----
    def _thumb_pixmap(self, img):
        img = img.scaled(THUMB_W * 2, THUMB_H * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return img, img.width() / 2, img.height() / 2

    def _thumbnail(self, layer):
        """Layer thumbnail (folder for groups), plus the mask thumbnail when there is one.
        The part that painting tools currently edit gets a blue frame."""
        pm = QPixmap((THUMB_W * 2 + 6) * 2, THUMB_H * 2)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        editing_mask = layer is self.active and self.canvas.edit_mask and layer.mask is not None
        parts = [("layer", None)] + ([("mask", mask_to_gray(layer.mask))] if layer.mask is not None else [])
        for n, (part, img) in enumerate(parts):
            x0 = n * (THUMB_W + 6)
            if part == "layer" and layer.kind == "group":
                theme.icon("folder", THUMB_H).paint(p, QRect(x0 + (THUMB_W - THUMB_H) // 2, 0, THUMB_H, THUMB_H))
                continue
            src = img if img is not None else self.doc.layer_image(layer, masked=False)
            thumb, w, h = self._thumb_pixmap(src)
            x, y = x0 + (THUMB_W - w) / 2, (THUMB_H - h) / 2
            if part == "layer":
                p.fillRect(QRectF(x, y, w, h), QColor("#ffffff"))  # paper behind transparency
            p.drawImage(QRectF(x, y, w, h), thumb)
            active_part = (part == "mask") == editing_mask and layer is self.active and len(parts) > 1
            p.setPen(QColor(theme.ACCENT) if active_part else QColor("#55555e"))
            p.drawRect(QRectF(x, y, w - 1, h - 1))
        p.end()
        return QIcon(pm)

    def refresh_layers(self):
        tree = self.layer_tree
        tree.blockSignals(True)
        tree.clear()
        current = None

        def add(layers, parent):
            nonlocal current
            for layer in reversed(layers):  # top layer first
                it = QTreeWidgetItem(parent)
                it.setData(0, Qt.UserRole, layer)
                it.setText(0, layer.name)
                it.setIcon(0, self._thumbnail(layer))
                it.setToolTip(0, f"{layer.kind} layer" + (" with mask" if layer.mask is not None else ""))
                it.setIcon(1, theme.icon("eye" if layer.visible else "eye_off", 18))
                it.setToolTip(1, "Show/hide")
                it.setFlags(it.flags() | Qt.ItemIsEditable)
                if layer.kind == "vector":
                    f = it.font(0)
                    f.setItalic(True)  # vector layers read as italic
                    it.setFont(0, f)
                if layer.kind == "group":
                    add(layer.children, it)
                    it.setExpanded(layer.expanded)
                if layer is self.active:
                    current = it

        add(self.doc.layers, tree)
        if current is not None:
            tree.setCurrentItem(current, 0)
        tree.blockSignals(False)
        self._sync_layer_props()

    def _on_current_item(self, item, _prev):
        if item is not None:
            self.active = item.data(0, Qt.UserRole)
            self.canvas.layer_changed()
            if self.active.mask is None:
                self.set_edit_mask(False)
            self._sync_layer_props()
            self._refresh_thumbs()  # move the blue "editing" frame

    def _refresh_thumbs(self):
        """Redraw thumbnails in place (safe inside tree signals, unlike a full rebuild)."""
        tree = self.layer_tree
        tree.blockSignals(True)
        todo = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while todo:
            item = todo.pop()
            item.setIcon(0, self._thumbnail(item.data(0, Qt.UserRole)))
            todo += [item.child(i) for i in range(item.childCount())]
        tree.blockSignals(False)

    def _on_item_clicked(self, item, column):
        if column == 1:  # the eye; deferred so the tree isn't rebuilt inside its own signal
            layer = item.data(0, Qt.UserRole)
            QTimer.singleShot(0, lambda: self.stack.push(
                SetAttrs(layer, {"visible": not layer.visible}, "Show/hide layer")))

    def _on_item_renamed(self, item, column):
        if column == 0:
            item.data(0, Qt.UserRole).name = item.text(0)

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
        has_mask = layer is not None and layer.mask is not None
        self.mask_btn.setChecked(has_mask and self.canvas.edit_mask)
        self.mask_btn.setToolTip("Edit mask (toggle)" if has_mask else "Add layer mask")

    # ---- layer operations ----
    def _insert_point(self):
        """(list, index) for a new layer: just above the active one."""
        if self.active is None:
            return self.doc.layers, len(self.doc.layers)
        lst = self.doc.parent_list(self.active)
        return lst, lst.index(self.active) + 1

    def _add_layer(self, layer, text):
        lst, i = self._insert_point()
        self.active = layer
        self.stack.push(Splice(lst, i, [], [layer], text))

    def _next_name(self, kind):
        self._layer_counter += 1
        return f"{kind} {self._layer_counter}"

    def add_raster_layer(self):
        self._add_layer(RasterLayer(self._next_name("Raster"), self.doc.width, self.doc.height), "New raster layer")

    def add_vector_layer(self):
        self._add_layer(VectorLayer(self._next_name("Vector")), "New vector layer")

    def add_group(self):
        self._add_layer(GroupLayer(self._next_name("Group")), "New group")

    def duplicate_layer(self):
        if self.active is not None:
            self._add_layer(self.active.clone(f"{self.active.name} copy"), "Duplicate layer")

    def delete_layer(self):
        if self.active is not None:
            lst = self.doc.parent_list(self.active)
            self.stack.push(Splice(lst, lst.index(self.active), [self.active], [], "Delete layer"))

    def move_layer(self, delta):
        """Move up (+1) or down (-1). Moving into a neighbouring group enters it; moving past
        the edge of a group leaves it."""
        layer = self.active
        if layer is None:
            return
        lst = self.doc.parent_list(layer)
        j = lst.index(layer) + delta
        if 0 <= j < len(lst):
            neighbour = lst[j]
            if neighbour.kind == "group":
                dst, k = neighbour.children, (0 if delta > 0 else len(neighbour.children))
                neighbour.expanded = True
            else:
                dst, k = lst, j
        else:
            group = self.doc.parent_group(layer)
            if group is None:
                return
            dst = self.doc.parent_list(group)
            k = dst.index(group) + (1 if delta > 0 else 0)
        self.stack.push(Relocate(layer, lst, dst, k, "Move layer"))

    def _bracket(self, how):
        if not self.canvas.arrange(how):
            self.move_layer(1 if how == "forward" else -1)

    def group_layer(self):
        if self.active is not None:
            lst = self.doc.parent_list(self.active)
            group = GroupLayer(self._next_name("Group"), [self.active])
            self.stack.push(Splice(lst, lst.index(self.active), [self.active], [group], "Group layer"))
            self.active = group
            self.refresh_layers()

    def ungroup(self):
        g = self.active
        if g is None or g.kind != "group":
            self.statusBar().showMessage("Select a group to ungroup.", 3000)
            return
        lst = self.doc.parent_list(g)
        self.active = g.children[-1] if g.children else None
        self.stack.push(Splice(lst, lst.index(g), [g], list(g.children), "Ungroup"))

    def merge_down(self):
        layer = self.active
        if layer is None:
            return
        lst = self.doc.parent_list(layer)
        i = lst.index(layer)
        if i == 0:
            self.statusBar().showMessage("There is no layer below to merge into.", 3000)
            return
        below = lst[i - 1]
        merged = RasterLayer(below.name, 0, 0, self.doc.flatten([below, layer]))
        self.active = merged
        self.stack.push(Splice(lst, i - 1, [below, layer], [merged], "Merge down"))

    # ---- masks ----
    def add_mask(self):
        layer = self.active
        if layer is None or layer.mask is not None:
            return
        mask = blank_mask(self.doc.width, self.doc.height)
        if self.canvas.selection is not None:  # like "reveal selection": hide everything else
            mask.fill(QColor(0, 0, 0, 0))
            p = QPainter(mask)
            p.setRenderHint(QPainter.Antialiasing)
            p.fillPath(self.canvas.selection, QColor(0, 0, 0, 255))
            p.end()
        self.stack.push(SetAttrs(layer, {"mask": mask}, "Add layer mask"))
        self.set_edit_mask(True)

    def delete_mask(self):
        if self.active is not None and self.active.mask is not None:
            self.set_edit_mask(False)
            self.stack.push(SetAttrs(self.active, {"mask": None}, "Delete layer mask"))

    def set_edit_mask(self, on):
        on = bool(on) and self.active is not None and self.active.mask is not None
        self.canvas.edit_mask = on
        self.edit_mask_action.setChecked(on)
        self.mask_btn.setChecked(on)
        self.statusBar().showMessage("Painting edits the layer mask." if on else "", 3000)
        self._refresh_thumbs()

    def _mask_button(self):
        if self.active is not None and self.active.mask is None:
            self.add_mask()
        else:
            self.set_edit_mask(not self.canvas.edit_mask)

    # ---- clipboard ----
    def _selection_rect(self):
        full = QRect(0, 0, self.doc.width, self.doc.height)
        sel = self.canvas.selection
        return sel.boundingRect().toAlignedRect().intersected(full) if sel is not None else full

    def _crop(self, img, rect, clip=True):
        out = QImage(rect.size(), img.format())
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.translate(-rect.x(), -rect.y())
        if clip and self.canvas.selection is not None:
            p.setClipPath(self.canvas.selection)
        p.drawImage(0, 0, img)
        p.end()
        return out

    def copy(self, cut=False):
        layer = self.active
        if layer is None:
            return
        mime = QMimeData()
        if layer.kind == "vector" and not self.canvas.edit_mask:
            s = self.canvas.selected_shape
            shapes = [s] if s is not None else list(layer.shapes)
            if not shapes:
                return
            self._clip_shapes = [x.clone() for x in shapes]
            tmp = VectorLayer("clip")
            tmp.shapes = self._clip_shapes
            rect = QRect(0, 0, self.doc.width, self.doc.height)
            mime.setImageData(self.doc.layer_image(tmp))  # other apps get pixels
            mime.setData(CLIP_SHAPES, b"1")
            if cut:
                self.stack.beginMacro("Cut")
                for x in shapes:
                    self.stack.push(RemoveShape(layer, x))
                self.stack.endMacro()
        else:
            src = (mask_to_gray(layer.mask) if self.canvas.edit_mask and layer.mask is not None
                   else self.doc.layer_image(layer))
            rect = self._selection_rect()
            if rect.isEmpty():
                return
            mime.setImageData(self._crop(src, rect))
            if cut:
                self.canvas.delete_selected()
        mime.setData(CLIP_OFFSET, f"{rect.x()},{rect.y()}".encode())
        QApplication.clipboard().setMimeData(mime)

    def paste(self):
        md = QApplication.clipboard().mimeData()
        if md is None:
            return
        if md.hasFormat(CLIP_SHAPES) and self._clip_shapes:
            self.stack.beginMacro("Paste")
            if self.active is None or self.active.kind != "vector":
                self.add_vector_layer()
            for s in self._clip_shapes:
                self.stack.push(AddShape(self.active, s.clone(), "Paste shape"))
            self.stack.endMacro()
            return
        if not md.hasImage():
            self.statusBar().showMessage("Nothing to paste.", 3000)
            return
        img = QImage(md.imageData())
        if md.hasFormat(CLIP_OFFSET):  # our own copy: paste in place
            x, y = (int(v) for v in bytes(md.data(CLIP_OFFSET)).decode().split(","))
        else:  # from another app: centre it
            x, y = (self.doc.width - img.width()) // 2, (self.doc.height - img.height()) // 2
        content = blank_image(self.doc.width, self.doc.height)
        p = QPainter(content)
        p.drawImage(x, y, img)
        p.end()
        self._add_layer(RasterLayer("Pasted", 0, 0, content), "Paste")

    # ---- image ----
    def canvas_size(self):
        dlg = SizeDialog(self, "Canvas Size", self.doc.width, self.doc.height)
        if dlg.exec():
            w, h = dlg.w.value(), dlg.h.value()
            if (w, h) != (self.doc.width, self.doc.height):  # keep the picture centred
                rect = QRect((self.doc.width - w) // 2, (self.doc.height - h) // 2, w, h)
                self.stack.push(ResizeCanvas(self.doc, rect, "Canvas size"))

    def crop_to_selection(self):
        if self.canvas.selection is None:
            self.statusBar().showMessage("Make a selection to crop to.", 3000)
            return
        rect = self._selection_rect()
        if not rect.isEmpty():
            self.stack.push(ResizeCanvas(self.doc, rect, "Crop"))
            self.canvas.deselect()

    def zoom_to_fit(self):
        vp = self.scroll.viewport().size()
        self.canvas.set_zoom(min(vp.width() / self.doc.width, vp.height() / self.doc.height) * 0.95)

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

    # ---- document ----
    def set_document(self, doc, path=None):
        self.doc = doc
        self.path = path  # .ora file this document saves to, if any
        self.stack.clear()
        self.active = doc.layers[-1] if doc.layers else None
        self.canvas.set_document(doc)
        self.set_edit_mask(False)
        self._update_status()
        self._on_doc_changed()
        self.setWindowModified(False)

    def _on_doc_changed(self, *_):
        if not self.doc.contains(self.active):
            self.active = self.doc.layers[-1] if self.doc.layers else None
        if self.canvas.edit_mask and (self.active is None or self.active.mask is None):
            self.set_edit_mask(False)
        self.refresh_layers()
        self.canvas.sync()
        self._update_status()

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
        if not self._confirm_discard():
            return
        dlg = SizeDialog(self, "New Document", 1200, 800, background=True)
        if dlg.exec():
            bg = Qt.white if dlg.bg.currentIndex() == 0 else None
            self.set_document(Document.new_default(dlg.w.value(), dlg.h.value(), bg))

    def open_file(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open", "", "PixStation / OpenRaster or image (*.ora *.png *.jpg *.jpeg *.bmp)")
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
        self.setWindowTitle(f"{name}[*] — PixStation")
        super().setWindowModified(modified)

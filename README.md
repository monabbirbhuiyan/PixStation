# PixStation

A small desktop image editor that mixes raster painting and vector drawing in one layered document. Built with Python and PySide6 (Qt 6).

## Run

Requires Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pixstation
```

Run the headless smoke test:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest
```

## Features

- **Layers**: raster and vector layers. Add (`+R` / `+V`), reorder (▲ ▼ or Ctrl+] / Ctrl+[), delete, hide (checkbox), rename (double-click). Each layer has an **opacity** slider and a **blend mode** (Normal, Multiply, Screen, Overlay, Darken, Lighten, Color Dodge/Burn, Hard/Soft Light, Difference, Exclusion).
- **Selections** (any layer): Marquee (M) and Lasso (L). Painting, erasing, filters and Clear stay inside the selection. Click without dragging, or Ctrl+D, to deselect; Ctrl+A selects all.
- **Raster tools**: Brush (B), Eraser (E). They only work on a raster layer.
- **Filter menu** (raster layers, respects the selection): Gaussian Blur, Sharpen, Brightness/Contrast, Hue/Saturation with live preview; Invert, Grayscale.
- **Vector tools** (vector layers only):
  - Select (V): click a shape to select it, drag to move, drag a corner handle to resize (hold Shift to keep proportions), Delete to remove. While a shape is selected, the Color / Fill / Fill shapes / Size controls change that shape.
  - Rectangle (R), Ellipse (O).
  - Pen (P): click for a corner point, press and drag for a smooth curve point. Click the first point to close; double-click, Enter or switching tools finishes an open path; Esc cancels.
  - Text (T): click to place text; choose the typeface with **Font…**. Text is stored as outlines, so it scales cleanly but can't be retyped after placing.
  - "Fill shapes" fills new shapes with the Fill color; Size 0 means no outline.
- **Undo/redo** for everything that changes the document (Cmd/Ctrl+Z, Cmd/Ctrl+Shift+Z). Slider and size tweaks merge into one undo step.
- **Project files**: Save (Cmd/Ctrl+S) and Save As (Cmd/Ctrl+Shift+S) write [OpenRaster](https://www.openraster.org/) `.ora`, an open layered format that Krita, GIMP and MyPaint can also open. Layers, names, visibility, opacity and blend modes are kept. Vector layers are saved twice: as a PNG that other apps can show, and as editable shapes in `data/layerN.json` (via a `pixstation:vector` attribute that other apps ignore).
- **Open**: `.ora`, PNG, JPG, BMP. Layer groups in other apps' `.ora` files are flattened.
- **Export**: flattened PNG, and SVG (Cmd/Ctrl+Shift+E). In SVG, vector layers become `<path>`s and raster layers are embedded as images. SVG keeps layer opacity but not blend modes.
- **Workspace**: dark theme, tool strip on the left, options bar for the current tool on top, Layers panel with thumbnails and a History panel (click a step to jump back to it) on the right, zoom and canvas size in the status bar. Icons are drawn in code in `pixstation/theme.py`.
- Zoom: Cmd/Ctrl+= / Cmd/Ctrl+- / Cmd/Ctrl+0, or Cmd/Ctrl + scroll wheel.

## Layout

- `pixstation/document.py`: document model, layers, shapes, rendering, undo commands
- `pixstation/canvas.py`: canvas widget and tool input
- `pixstation/filters.py`: raster filters (Pillow/NumPy) and the filter dialog
- `pixstation/ora.py`: OpenRaster save/load
- `pixstation/theme.py`: dark theme and icon set
- `pixstation/window.py`: main window, toolbar, layer panel, menus, file I/O

## Not yet

Layer groups, layer masks, moving or transforming pixels, editing individual path points after drawing, re-editable text, gradients, and an eye-icon visibility toggle.

## License

Copyright (C) 2026 the PixStation contributors.

PixStation is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE) for the full text.

Dependencies keep their own licenses: PySide6 (LGPL-3.0), Pillow (MIT-CMU), NumPy (BSD-3-Clause).

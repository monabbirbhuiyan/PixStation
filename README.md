# Strata

A small desktop image editor that mixes raster painting and vector drawing in one layered document. Built with Python and PySide6 (Qt 6).

## Run

Requires Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m strata
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
- **Project files**: Save (Cmd/Ctrl+S) and Save As (Cmd/Ctrl+Shift+S) write [OpenRaster](https://www.openraster.org/) `.ora`, an open layered format that Krita, GIMP and MyPaint can also open. Layers, names, visibility, opacity and blend modes are kept. Vector layers are saved twice: as a PNG that other apps can show, and as editable shapes in `data/layerN.json` (via a `strata:vector` attribute that other apps ignore).
- **Open**: `.ora`, PNG, JPG, BMP. Layer groups in other apps' `.ora` files are flattened.
- **Export**: flattened PNG, and SVG (Cmd/Ctrl+Shift+E). In SVG, vector layers become `<path>`s and raster layers are embedded as images. SVG keeps layer opacity but not blend modes.
- Zoom: Cmd/Ctrl+= / Cmd/Ctrl+- / Cmd/Ctrl+0.

## Layout

- `strata/document.py`: document model, layers, shapes, rendering, undo commands
- `strata/canvas.py`: canvas widget and tool input
- `strata/filters.py`: raster filters (Pillow/NumPy) and the filter dialog
- `strata/ora.py`: OpenRaster save/load
- `strata/window.py`: main window, toolbar, layer panel, menus, file I/O

## Not yet

Layer groups, layer masks, moving or transforming pixels, editing individual path points after drawing, re-editable text, gradients, and a history panel.

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

Shortcuts use Cmd on macOS and Ctrl elsewhere.

**Layers**
- Raster, vector and **group** layers in a tree. Buttons under the panel: new raster, new vector, new group, mask, move up, move down, delete.
- Click the eye to show or hide a layer; double-click a name to rename it.
- Moving a layer up or down steps into a neighbouring group, or out past the edge of the one it's in.
- Layer menu: Duplicate (Ctrl+J), Group Layer (Ctrl+G), Ungroup (Ctrl+Shift+G), Merge Down (Ctrl+E).
- Each layer or group has an **opacity** slider and a **blend mode** (Normal, Multiply, Screen, Overlay, Darken, Lighten, Color Dodge/Burn, Hard/Soft Light, Difference, Exclusion).
- **Layer masks**: the mask button adds one (if there's a selection, only the selected area stays visible). With a mask, the same button, or Layer › Edit Mask (`\`), switches the painting tools between the layer and its mask; a blue frame marks which one you're editing. On a mask, black hides, white shows and grey is partial.

**Tools** (the options bar shows the settings and a hint for the current tool)
- Move (Shift+V): drags the whole layer or group (pixels, mask and shapes together), or only the selected pixels if there is a selection.
- Select (V): click a shape to select it, drag to move, drag a corner to resize (Shift keeps proportions), Delete removes it. Double-click text to edit it. While a shape is selected, Stroke / Fill / Fill shapes / Size / Font change that shape.
- Direct Select (A): click a shape, then drag its individual points and curve handles.
- Marquee (M), Lasso (L): pixel selections. Painting, fills, gradients, filters, Clear and copy stay inside the selection. Click once or press Ctrl+D to deselect; Ctrl+A selects all.
- Brush (B), Eraser (E): paint on a raster layer or on a mask.
- Paint Bucket (K): fills connected pixels of similar colour; set how similar with Tolerance.
- Gradient (G): drag to fill from the Color to the To colour.
- Eyedropper (I): picks a colour from the image.
- Rectangle (R), Ellipse (O), Pen (P): vector shapes. With Pen, click for corners and press-drag for curves; click the first point to close; double-click, Enter or switching tools finishes; Esc cancels.
- Text (T): click to add text, or click existing text to change it. Text stays editable and re-renders when you change its font.

**Menus**
- Edit: Undo/Redo, Cut/Copy/Paste, Clear. Copying works within PixStation and between apps: pixels paste in place as a new layer, shapes paste as editable shapes, and images from other apps paste centred.
- Image: Canvas Size… (keeps the picture centred) and Crop to Selection.
- Object: Bring to Front (Ctrl+Shift+]), Bring Forward (Ctrl+]), Send Backward (Ctrl+[), Send to Back (Ctrl+Shift+[), Align to Canvas. With no shape selected, Ctrl+] / Ctrl+[ move the layer instead.
- Filter (raster layers): Gaussian Blur, Sharpen, Brightness/Contrast and Hue/Saturation with live preview; Invert, Grayscale.
- View: Zoom In/Out, Fit on Screen (Ctrl+0), Actual Size (Ctrl+1), Ctrl + scroll wheel, show/hide History.

**Files**
- New… asks for a size and a white or transparent background.
- Save (Ctrl+S) and Save As (Ctrl+Shift+S) write [OpenRaster](https://www.openraster.org/) `.ora`, an open layered format that Krita, GIMP and MyPaint can also open. Groups, names, visibility, opacity and blend modes are standard OpenRaster. Each layer's PNG shows how it looks (masks applied), so other apps display the right picture; PixStation also stores the editable parts (vector shapes and text, masks, and the unmasked pixels) in extra files that other apps ignore.
- Open: `.ora`, PNG, JPG, BMP.
- Export: flattened PNG, and SVG (Ctrl+Shift+E). In SVG, vector layers become `<path>`s and raster or masked layers are embedded as images. SVG keeps opacity but not blend modes.

**Everything** that changes the document can be undone; slider and size tweaks merge into one step, and the History panel jumps to any step.

## Layout

- `pixstation/document.py`: document model (layer tree, masks, shapes, text), rendering, undo commands
- `pixstation/canvas.py`: canvas widget and tool input
- `pixstation/filters.py`: raster filters, paint-bucket fill (Pillow/NumPy), filter dialog
- `pixstation/ora.py`: OpenRaster save/load
- `pixstation/theme.py`: dark theme and icon set
- `pixstation/window.py`: main window, toolbar, layer panel, menus, file I/O

## Not yet

Free transform and rotation for pixels, image resampling (Image Size), brush hardness and pressure, adjustment layers, and drag-and-drop in the Layers panel.

## License

Copyright (C) 2026 the PixStation contributors.

PixStation is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE) for the full text.

Dependencies keep their own licenses: PySide6 (LGPL-3.0), Pillow (MIT-CMU), NumPy (BSD-3-Clause).

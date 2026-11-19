"""Headless end-to-end check: paint, draw shapes, reorder, undo, save.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest
"""
import os
import tempfile
import unittest
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainterPath
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from strata.document import RasterLayer, blank_image
from strata.filters import invert
from strata.ora import load_ora
from strata.window import MainWindow

app = QApplication.instance() or QApplication([])


class SmokeTest(unittest.TestCase):
    def setUp(self):
        self.win = MainWindow()
        self.win.show()
        self.c = self.win.canvas
        self.doc = self.win.doc

    def drag(self, a, b):
        QTest.mousePress(self.c, Qt.LeftButton, Qt.NoModifier, QPoint(*a))
        QTest.mouseMove(self.c, QPoint(*b))
        QTest.mouseRelease(self.c, Qt.LeftButton, Qt.NoModifier, QPoint(*b))

    def test_workflow(self):
        bg, vec = self.doc.layers
        # brush on raster layer
        self.win.active = bg
        self.c.set_tool("brush")
        self.drag((100, 100), (200, 100))
        self.assertNotEqual(bg.image.pixelColor(150, 100).name(), "#ffffff")
        # eraser clears to transparent
        self.c.set_tool("eraser")
        self.drag((150, 90), (150, 110))
        self.assertEqual(bg.image.pixelColor(150, 100).alpha(), 0)
        # vector shapes
        self.win.active = vec
        for tool in ("rect", "ellipse"):
            self.c.set_tool(tool)
            self.drag((300, 300), (400, 380))
        self.c.set_tool("pen")
        for pt in [(500, 500), (600, 500), (600, 600)]:
            QTest.mouseClick(self.c, Qt.LeftButton, Qt.NoModifier, QPoint(*pt))
        QTest.mouseClick(self.c, Qt.LeftButton, Qt.NoModifier, QPoint(502, 502))  # near start → close
        self.assertEqual(len(vec.shapes), 3)
        # wrong layer type is refused
        self.c.set_tool("brush")
        self.drag((10, 10), (20, 20))
        self.assertEqual(len(vec.shapes), 3)
        # reorder + undo
        self.win.move_layer(-1)
        self.assertIs(self.doc.layers[0], vec)
        self.win.stack.undo()
        self.assertIs(self.doc.layers[0], bg)
        self.win.stack.undo()  # pen path
        self.assertEqual(len(vec.shapes), 2)
        self.win.stack.redo()
        self.assertEqual(len(vec.shapes), 3)
        for _ in range(3):
            self.win.stack.undo()  # pen, ellipse, rect
        self.win.stack.undo()  # eraser
        self.assertGreater(bg.image.pixelColor(150, 100).alpha(), 0)
        self.win.stack.redo()
        # save
        with tempfile.TemporaryDirectory() as d:
            png, svg = os.path.join(d, "out.png"), os.path.join(d, "out.svg")
            self.assertTrue(self.doc.flatten().save(png))
            self.assertEqual(QImage(png).size().width(), self.doc.width)
            self.win.stack.redo()  # bring rect back for SVG
            self.doc.export_svg(svg)
            with open(svg) as f:
                self.assertIn("<path", f.read())
        self.win.stack.setClean()  # skip discard prompt on close

    def test_select_move_scale_restyle_delete(self):
        vec = self.doc.layers[1]
        self.win.active = vec
        self.c.set_tool("rect")
        self.drag((100, 100), (200, 200))
        shape = vec.shapes[0]
        self.c.set_tool("select")
        self.drag((100, 150), (130, 170))  # grab the left edge, move by (30, 20)
        self.assertIs(self.c.selected_shape, shape)
        self.assertEqual(shape.path.boundingRect().topLeft().toTuple(), (130, 120))
        self.drag((230, 220), (330, 320))  # bottom-right handle: doubles the size
        r = shape.path.boundingRect()
        self.assertEqual((r.width(), r.height()), (200, 200))
        self.win.size_spin.setValue(3)  # toolbar edits restyle the selection...
        self.win.size_spin.setValue(4)
        self.assertEqual(shape.width, 4)
        self.win.stack.undo()  # ...and consecutive edits merge into one undo step
        self.assertEqual(shape.width, 8)
        self.win.stack.undo()  # resize
        self.win.stack.undo()  # move
        self.assertEqual(shape.path.boundingRect().topLeft().toTuple(), (100, 100))
        self.c.delete_selected()
        self.assertEqual(vec.shapes, [])
        self.win.stack.undo()
        self.assertEqual(vec.shapes, [shape])
        self.win.stack.setClean()

    def test_bezier_pen_and_text(self):
        vec = self.doc.layers[1]
        self.win.active = vec
        self.c.set_tool("pen")
        self.drag((100, 100), (150, 50))  # press-drag = curve handle
        QTest.mouseClick(self.c, Qt.LeftButton, Qt.NoModifier, QPoint(300, 100))
        self.c.set_tool("text")  # switching tools finishes the open path
        path = vec.shapes[0].path
        kinds = {path.elementAt(i).type for i in range(path.elementCount())}
        self.assertIn(QPainterPath.CurveToElement, kinds)
        self.c.set_tool("text")
        self.c.ask_text = lambda: "Hi"
        QTest.mouseClick(self.c, Qt.LeftButton, Qt.NoModifier, QPoint(100, 300))
        self.assertEqual(len(vec.shapes), 2)
        self.assertFalse(vec.shapes[1].path.isEmpty())
        self.win.stack.setClean()

    def test_selection_limits_paint_and_filters(self):
        bg = self.doc.layers[0]
        self.win.active = bg
        self.c.set_tool("marquee")
        self.drag((1, 1), (100, 799))  # QTest treats (0,0) as "widget centre"
        self.c.set_tool("brush")
        self.drag((50, 400), (300, 400))
        self.assertNotEqual(bg.image.pixelColor(60, 400).name(), "#ffffff")
        self.assertEqual(bg.image.pixelColor(200, 400).name(), "#ffffff")  # outside selection
        self.win.run_filter("Invert", invert, [])
        self.assertEqual(bg.image.pixelColor(10, 10).name(), "#000000")
        self.assertEqual(bg.image.pixelColor(500, 10).name(), "#ffffff")
        self.win.stack.undo()
        self.assertEqual(bg.image.pixelColor(10, 10).name(), "#ffffff")
        self.c.deselect()
        self.win.stack.setClean()

    def test_opacity_and_blend(self):
        bg = self.doc.layers[0]
        top = RasterLayer("Top", self.doc.width, self.doc.height, blank_image(self.doc.width, self.doc.height, QColor(255, 0, 0)))
        self.doc.layers.append(top)
        self.win.active = top
        self.win.refresh_layers()
        self.win.opacity.setValue(50)
        self.assertEqual(top.opacity, 0.5)
        self.assertAlmostEqual(self.doc.flatten().pixelColor(5, 5).green(), 127, delta=1)  # half red over white
        self.win.opacity.setValue(100)
        self.win.blend.setCurrentIndex(self.win.blend.findData("multiply"))
        bg.image.fill(QColor(0, 0, 255))
        self.assertEqual(self.doc.flatten().pixelColor(5, 5).name(), "#000000")  # red × blue
        self.win.stack.setClean()

    def test_ora_round_trip(self):
        bg, vec = self.doc.layers
        self.win.active = bg
        self.c.set_tool("brush")
        self.drag((100, 100), (200, 100))
        self.win.active = vec
        self.c.fill_enabled = True
        self.c.set_tool("ellipse")
        self.drag((300, 300), (400, 380))
        vec.visible = False
        vec.opacity, vec.blend = 0.4, "screen"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.ora")
            self.assertTrue(self.win._write_ora(path))
            self.assertTrue(self.win.stack.isClean())
            with zipfile.ZipFile(path) as z:  # spec: mimetype first and uncompressed
                first = z.infolist()[0]
                self.assertEqual((first.filename, first.compress_type), ("mimetype", zipfile.ZIP_STORED))
                self.assertIn("mergedimage.png", z.namelist())
            doc = load_ora(path)
        self.assertEqual([l.kind for l in doc.layers], ["raster", "vector"])
        self.assertEqual(doc.layers[0].image, bg.image)
        self.assertFalse(doc.layers[1].visible)
        self.assertEqual((doc.layers[1].opacity, doc.layers[1].blend), (0.4, "screen"))
        a, b = vec.shapes[0], doc.layers[1].shapes[0]
        self.assertEqual(a.path, b.path)
        self.assertEqual((a.stroke, a.fill, a.width), (b.stroke, b.fill, b.width))
        with tempfile.NamedTemporaryFile(suffix=".ora") as f:
            f.write(b"not a zip")
            f.flush()
            with self.assertRaises(ValueError):
                load_ora(f.name)


if __name__ == "__main__":
    unittest.main()

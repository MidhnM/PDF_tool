import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from pathlib import Path
import pymupdf as fitz
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from main import QApplication, Editor


class GUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_selection_preview_worker_and_undo(self):
        w=Editor()
        path=w.fresh()
        with fitz.open() as d:
            for n in range(3):
                p=d.new_page(width=400,height=500)
                p.insert_text((40,60),'A live preview sample')
            d.save(path)
        w.load(path)
        w.show()
        self.app.processEvents()
        QTest.mousePress(w.canvas,Qt.LeftButton,pos=QPoint(40,50))
        QTest.mouseMove(w.canvas,QPoint(360,450))
        self.assertIsNotNone(w.canvas.selection)
        self.assertFalse(w.preview.pixmap().isNull())
        QTest.mouseRelease(w.canvas,Qt.LeftButton,pos=QPoint(360,450))
        w.page.setValue(2)
        self.assertEqual(w.canvas.selection,(.1,.1,.9,.9))
        w.mutate('crop',area=True)
        for _ in range(200):
            QTest.qWait(10)
            if not w.busy() and w.centralWidget().isEnabled():
                break
        self.assertEqual(w.doc[0].rect,fitz.Rect(0,0,320,400))
        self.assertEqual(w.doc[2].rect,fitz.Rect(0,0,320,400))
        w.undo()
        self.assertEqual(w.doc[0].rect,fitz.Rect(0,0,400,500))
        w.redo()
        self.assertEqual(w.doc[0].rect,fitz.Rect(0,0,320,400))
        w.dirty=False
        w.close()


if __name__=='__main__':
    unittest.main()

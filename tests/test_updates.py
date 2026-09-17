import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import pymupdf as fitz
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from main import Editor
from widgets import Canvas, SavePreviewDialog, page_pixmap
from engine import edit, apply_overlay


class Updates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.w=Editor()
        self.source=self.w.fresh()
        with fitz.open() as d:
            for angle in (0,90,180,270):
                p=d.new_page(width=400,height=500)
                p.insert_text((40,60),'Highlight this text',fontsize=14)
                p.insert_text((40,110),'Keep this line unchanged',fontsize=12)
                p.set_rotation(angle)
            d.save(self.source)
        self.w.load(self.source)
        self.w.show()
        self.app.processEvents()

    def tearDown(self):
        self.w.preview_timer.stop()
        self.w.dirty=False
        self.w.close()

    def drag(self,start,end):
        c=self.w.canvas
        QTest.mousePress(c,Qt.LeftButton,pos=QPoint(*start))
        QTest.mouseMove(c,QPoint(*end))
        QTest.mouseRelease(c,Qt.LeftButton,pos=QPoint(*end))

    def test_resize_move_and_zoom(self):
        c=self.w.canvas
        c.selection=(.2,.2,.8,.8)
        self.drag((320,400),(360,450))
        self.assertEqual(c.selection,(.2,.2,.9,.9))
        self.drag((80,250),(40,250))
        self.assertEqual(c.selection,(.1,.2,.9,.9))
        self.drag((200,250),(160,200))
        for actual,expected in zip(c.selection,(0,.1,.8,.8)):
            self.assertAlmostEqual(actual,expected)
        self.w.zoom.setValue(1.5)
        self.drag((480,600),(540,675))
        self.assertAlmostEqual(c.selection[2],.9)
        self.assertAlmostEqual(c.selection[3],.9)

    def test_autofit_exact_preview_and_reflow(self):
        w=self.w
        w.canvas.selection=(.1,.3,.9,.45)
        w.text.setPlainText('A longer paragraph that should wrap and adjust to the selected area. '*5)
        w.font_size.setValue(24)
        w.mode.setCurrentText('Text')
        w.render()
        self.assertIn('Text preview',w.overlay_status.text())
        with fitz.open() as d:
            d.insert_pdf(w.doc,from_page=0,to_page=0)
            message=apply_overlay(d[0],'text',w.canvas.selection,1,4,**w.overlay_options('text'))
            self.assertNotIn('24 pt',message)
            expected=page_pixmap(d[0],1)
            self.assertEqual(w.canvas.pix.toImage(),expected.toImage())
        w.canvas.selection=(.1,.3,.9,.9)
        w.render()
        self.assertIn('Text preview',w.overlay_status.text())
        self.assertNotIn('longer paragraph',w.doc[0].get_text())

    def test_highlight_comments_survive_save(self):
        out=self.w.fresh()
        # Highlight first line only on unrotated page.
        edit(self.source,out,'highlight',[0],(.08,.07,.8,.14),color=(.2,.85,.4),note='Check voltage')
        with fitz.open(out) as d:
            p=d[0]
            annotations=list(p.annots())
            self.assertEqual(len(annotations),1)
            self.assertEqual(annotations[0].type[0],fitz.PDF_ANNOT_HIGHLIGHT)
            self.assertEqual(annotations[0].info['content'],'Check voltage')
            self.assertIn('Highlight this text',p.get_text())
            self.assertEqual(len(list(d[1].annots() or [])),0)
        out2=self.w.fresh()
        edit(out,out2,'comment',list(range(4)),(.1,.3,.5,.5),note='Review each page')
        with fitz.open(out2) as d:
            for p in d:
                self.assertTrue(any(a.info['content']=='Review each page' for a in p.annots()))

    def test_scanned_highlight_and_rotated_preview(self):
        with fitz.open() as d:
            p=d.new_page(width=200,height=200)
            with self.assertRaises(ValueError):
                apply_overlay(p,'highlight',(.1,.1,.8,.8),1,1)
            apply_overlay(p,'highlight',(.1,.1,.8,.8),1,1,area_highlight=True,note='Scan')
            self.assertEqual(p.first_annot.type[0],fitz.PDF_ANNOT_SQUARE)
        self.w.page.setValue(2)
        self.w.canvas.selection=(.1,.3,.8,.6)
        self.w.mode.setCurrentText('Text')
        self.w.render()
        self.assertIn('Text preview',self.w.overlay_status.text())

    def test_save_scope_preview(self):
        dialog=SavePreviewDialog(self.w.doc,2,self.w)
        self.assertEqual(dialog.output_indices,[0,1,2,3])
        dialog.scope.setCurrentIndex(1)
        self.assertEqual(dialog.output_indices,[2])
        self.assertFalse(dialog.preview.pixmap().isNull())
        dialog.scope.setCurrentIndex(2)
        dialog.ranges.setText('4,1-2')
        self.assertEqual(dialog.output_indices,[3,0,1])
        dialog.page.setValue(2)
        self.assertIn('document page 1',dialog.count.text())
        dialog.ranges.setText('5')
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.close()

    def wait_job(self):
        for _ in range(300):
            QTest.qWait(10)
            if not self.w.busy() and self.w.centralWidget().isEnabled():
                return
        self.fail('Worker did not complete')

    def test_post_edit_prompt_and_subset_save_dirty(self):
        w=self.w
        calls=[]
        real_save=w.save_pdf
        w.save_pdf=lambda: calls.append('prompt')
        w.canvas.selection=(.1,.1,.9,.9)
        w.mutate('crop',area=True)
        self.wait_job()
        QTest.qWait(20)
        self.assertEqual(calls,['prompt'])
        w.save_pdf=real_save
        target=w.fresh()
        def choose_current(dialog):
            dialog.scope.setCurrentIndex(1)
            return QDialog.Accepted
        with patch.object(SavePreviewDialog,'exec',choose_current), patch.object(w,'output_path',return_value=target), patch.object(QMessageBox,'information'):
            w.save_pdf()
            self.wait_job()
        self.assertTrue(w.dirty)
        with fitz.open(target) as d:
            self.assertEqual(len(d),1)
            self.assertEqual(d[0].rect,fitz.Rect(0,0,320,400))
        target2=w.fresh()
        with patch.object(SavePreviewDialog,'exec',return_value=QDialog.Accepted), patch.object(w,'output_path',return_value=target2), patch.object(QMessageBox,'information'):
            w.save_pdf()
            self.wait_job()
        self.assertFalse(w.dirty)
        with fitz.open(target2) as d:
            self.assertEqual(len(d),4)

    def test_branding(self):
        self.assertFalse(self.w.windowIcon().isNull())
        self.assertIn('PDF Editor Tool',self.w.windowTitle())
        self.assertFalse(hasattr(self.w,'preview'))
        with patch.object(QMessageBox,'about') as about:
            self.w.about()
            self.assertIn('Midhun M',about.call_args.args[2])
            self.assertIn('1.0',about.call_args.args[2])


if __name__=='__main__':
    unittest.main()

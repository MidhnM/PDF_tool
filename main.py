"""Run this file from PyCharm or with: python main.py"""
import os
import sys
import tempfile
import uuid
from pathlib import Path
import pymupdf as fitz
from PySide6.QtCore import Qt, QRectF, Signal, QThread
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QAction
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QFileDialog, QMessageBox, QComboBox,
    QLineEdit, QSpinBox, QDoubleSpinBox, QScrollArea, QSplitter, QFormLayout,
    QGroupBox, QTextEdit, QInputDialog, QProgressBar, QCheckBox)
from engine import edit, extract, atomic_save, pages_from_text


class Canvas(QWidget):
    selected = Signal(object)

    def __init__(self):
        super().__init__()
        self.pix = QPixmap()
        self.selection = None
        self.anchor = None
        self.setCursor(Qt.CrossCursor)
        self.setMinimumSize(400, 500)

    def set_page(self, pix):
        self.pix = pix
        self.setFixedSize(pix.size())
        self.update()

    def fraction(self, p):
        return (max(0., min(1., p.x()/self.width())),
                max(0., min(1., p.y()/self.height())))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.pix.isNull():
            self.anchor = self.fraction(event.position())
            self.selection = None

    def mouseMoveEvent(self, event):
        if self.anchor is not None:
            x, y = self.fraction(event.position())
            a, b = self.anchor
            self.selection = (min(a,x), min(b,y), max(a,x), max(b,y))
            self.update()
            self.selected.emit(self.selection)

    def mouseReleaseEvent(self, event):
        if self.anchor is not None:
            self.mouseMoveEvent(event)
            self.anchor = None
            if self.selection and (self.selection[2]-self.selection[0] < .003 or self.selection[3]-self.selection[1] < .003):
                self.selection = None
            self.selected.emit(self.selection)
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('white'))
        p.drawPixmap(0, 0, self.pix)
        if self.selection:
            x0,y0,x1,y1 = self.selection
            r = QRectF(x0*self.width(), y0*self.height(), (x1-x0)*self.width(), (y1-y0)*self.height())
            shade = QColor(16, 24, 40, 125)
            p.fillRect(QRectF(0,0,self.width(),r.top()), shade)
            p.fillRect(QRectF(0,r.bottom(),self.width(),self.height()-r.bottom()), shade)
            p.fillRect(QRectF(0,r.top(),r.left(),r.height()), shade)
            p.fillRect(QRectF(r.right(),r.top(),self.width()-r.right(),r.height()), shade)
            p.setPen(QPen(QColor('#2563eb'), 2))
            p.drawRect(r)


class Worker(QThread):
    done = Signal(str)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            result = self.fn(lambda n,total: self.progress.emit(n,total))
            self.done.emit(result or '')
        except Exception as exc:
            self.failed.emit(str(exc))


class Editor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.temp = tempfile.TemporaryDirectory(prefix='pdf_editor_')
        self.path = None
        self.original = None
        self.doc = None
        self.history = []
        self.future = []
        self.dirty = False
        self.worker = None
        self.setWindowTitle('PDF Desk | Crop • Organize • Redact')
        self.resize(1280, 850)
        self.build_ui()

    def button(self, label, callback, layout):
        b = QPushButton(label)
        b.clicked.connect(lambda: self.safe(callback))
        layout.addWidget(b)
        return b

    def safe(self, fn):
        try:
            fn()
        except Exception as exc:
            QMessageBox.warning(self, 'Unable to complete', str(exc))

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        bar = QHBoxLayout()
        outer.addLayout(bar)
        for title, fn in [('Open PDF', self.open_pdf), ('Save copy', self.save_pdf),
                          ('Undo', self.undo), ('Redo', self.redo), ('Append PDFs', self.merge)]:
            self.button(title, fn, bar)
        bar.addStretch()
        self.filename = QLabel('Open a PDF to begin')
        bar.addWidget(self.filename)
        splitter = QSplitter()
        outer.addWidget(splitter, 1)
        left = QWidget()
        left.setMinimumWidth(300)
        left.setMaximumWidth(390)
        panel = QVBoxLayout(left)
        intro = QLabel('PDF DESK\nDraw once. Apply across pages.')
        intro.setStyleSheet('font-size:18px; font-weight:600; padding:10px 0;')
        panel.addWidget(intro)
        form = QFormLayout()
        self.scope = QComboBox()
        self.scope.addItems(['All pages', 'Current page', 'Page range'])
        self.ranges = QLineEdit('1')
        self.ranges.setPlaceholderText('1-3,5,8-10')
        form.addRow('Apply to', self.scope)
        form.addRow('Page range', self.ranges)
        panel.addLayout(form)
        hint = QLabel('Drag a box on the page. The same relative area is used on differently sized pages. Change page to inspect before applying.')
        hint.setWordWrap(True)
        panel.addWidget(hint)
        self.measure = QLabel('No area selected')
        panel.addWidget(self.measure)
        self.preview = QLabel('Live crop preview')
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedHeight(145)
        self.preview.setStyleSheet('background:#e2e8f0; border:1px solid #cbd5e1;')
        panel.addWidget(self.preview)
        self.button('Apply crop', lambda: self.mutate('crop', area=True), panel)
        self.black = QCheckBox('Black redaction fill (otherwise white)')
        panel.addWidget(self.black)
        self.button('Redact selected area — remove content', self.redact, panel)
        text_box = QGroupBox('Text / page numbers in selected box')
        tf = QVBoxLayout(text_box)
        self.text = QTextEdit('Page {page} of {pages}')
        self.text.setFixedHeight(70)
        tf.addWidget(self.text)
        self.font_size = QSpinBox()
        self.font_size.setRange(6, 144)
        self.font_size.setValue(12)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel('Font size (pt)'))
        size_row.addWidget(self.font_size)
        tf.addLayout(size_row)
        self.button('Add text', lambda: self.mutate('text', area=True,
            text=self.text.toPlainText(), size=self.font_size.value()), tf)
        panel.addWidget(text_box)
        tools = QComboBox()
        tools.addItems(['Choose another tool…', 'Extract selected pages', 'Split by page groups',
            'Rotate selected pages 90°', 'Delete selected pages', 'Reorder / duplicate pages',
            'Reset selected page crops', 'Export selected pages as PNG', 'Export selected text',
            'Clean metadata / attachments', 'Save password-protected copy'])
        tools.activated.connect(lambda i: self.safe(lambda: self.extra(i)))
        self.extras = tools
        panel.addWidget(tools)
        self.button('Clear selection', self.clear_selection, panel)
        panel.addStretch()
        splitter.addWidget(left)
        right = QWidget()
        rv = QVBoxLayout(right)
        nav = QHBoxLayout()
        self.button('◀', lambda: self.page.setValue(self.page.value()-1), nav)
        self.page = QSpinBox()
        self.page.setRange(1,1)
        self.page.valueChanged.connect(lambda: self.safe(self.render))
        nav.addWidget(self.page)
        self.total = QLabel('/ 0 pages')
        nav.addWidget(self.total)
        self.button('▶', lambda: self.page.setValue(self.page.value()+1), nav)
        nav.addStretch()
        nav.addWidget(QLabel('Zoom'))
        self.zoom = QDoubleSpinBox()
        self.zoom.setRange(.25, 3)
        self.zoom.setSingleStep(.25)
        self.zoom.setValue(1)
        self.zoom.valueChanged.connect(lambda: self.safe(self.render))
        nav.addWidget(self.zoom)
        rv.addLayout(nav)
        self.canvas = Canvas()
        self.canvas.selected.connect(self.selection_changed)
        scroll = QScrollArea()
        scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        scroll.setWidget(self.canvas)
        rv.addWidget(scroll)
        splitter.addWidget(right)
        splitter.setStretchFactor(1,1)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        outer.addWidget(self.progress)
        self.statusBar().showMessage('Files stay on your computer. Original PDFs remain unchanged.')
        for shortcut, fn in [('Ctrl+O',self.open_pdf), ('Ctrl+S',self.save_pdf), ('Ctrl+Z',self.undo), ('Ctrl+Y',self.redo)]:
            action = QAction(self)
            action.setShortcut(shortcut)
            action.triggered.connect(lambda checked=False, f=fn: self.safe(f) if not self.busy() else None)
            self.addAction(action)
        self.setStyleSheet('QPushButton {padding:7px;} QGroupBox {margin-top:8px; padding-top:12px;} QLineEdit,QComboBox,QSpinBox {padding:4px;}')

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def require(self):
        if not self.doc:
            raise ValueError('Open a PDF first.')

    def fresh(self):
        return str(Path(self.temp.name) / (uuid.uuid4().hex + '.pdf'))

    def discard_ok(self):
        return not self.dirty or QMessageBox.question(self, 'Unsaved changes',
            'Discard unsaved changes?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def open_pdf(self):
        if not self.discard_ok():
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Open PDF', '', 'PDF files (*.pdf)')
        if not path:
            return
        with fitz.open(path) as doc:
            if doc.needs_pass:
                pw, ok = QInputDialog.getText(self, 'Encrypted PDF', 'Password:', QLineEdit.Password)
                if not ok:
                    return
                if not doc.authenticate(pw):
                    raise ValueError('Incorrect password.')
            target = self.fresh()
            atomic_save(doc, target)
        old_paths = set(self.history + self.future + ([self.path] if self.path else []))
        self.original = str(Path(path).resolve())
        self.history, self.future = [], []
        self.load(target)
        for old in old_paths:
            Path(old).unlink(missing_ok=True)
        self.dirty = False
        self.filename.setText(Path(path).name)
        self.clear_selection()

    def load(self, path):
        if self.doc:
            self.doc.close()
        self.path = path
        self.doc = fitz.open(path)
        self.page.blockSignals(True)
        self.page.setMaximum(len(self.doc))
        self.page.blockSignals(False)
        self.total.setText(f'/ {len(self.doc)} pages')
        self.render()

    def render(self):
        if not self.doc:
            return
        page = self.doc[self.page.value()-1]
        # Limit the rendered bitmap for very large engineering pages.
        scale = min(self.zoom.value(), 2600/max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        self.canvas.set_page(QPixmap.fromImage(image))
        self.selection_changed(self.canvas.selection)

    def selection_changed(self, selection):
        if not selection or not self.doc:
            self.measure.setText('No area selected')
            self.preview.setPixmap(QPixmap())
            self.preview.setText('Live crop preview')
            return
        x0,y0,x1,y1 = selection
        page = self.doc[self.page.value()-1]
        self.measure.setText(f'Box: {(x1-x0)*page.rect.width:.1f} × {(y1-y0)*page.rect.height:.1f} pt')
        pix = self.canvas.pix
        cut = pix.copy(int(x0*pix.width()), int(y0*pix.height()),
                       max(1,int((x1-x0)*pix.width())), max(1,int((y1-y0)*pix.height())))
        self.preview.setPixmap(cut.scaled(280,135,Qt.KeepAspectRatio,Qt.SmoothTransformation))

    def clear_selection(self):
        self.canvas.selection = None
        self.canvas.update()
        self.selection_changed(None)

    def indices(self):
        self.require()
        if self.scope.currentIndex() == 0:
            return list(range(len(self.doc)))
        if self.scope.currentIndex() == 1:
            return [self.page.value()-1]
        return pages_from_text(self.ranges.text(), len(self.doc))

    def run_job(self, label, fn, callback):
        self.centralWidget().setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0,0)
        self.statusBar().showMessage(label)
        self.worker = Worker(fn)
        self.worker.progress.connect(self.show_progress)
        self.worker.done.connect(lambda result: self.safe(lambda: callback(result)))
        self.worker.failed.connect(lambda msg: QMessageBox.warning(self, 'Operation failed; document unchanged', msg))
        self.worker.finished.connect(self.job_finished)
        self.worker.start()

    def show_progress(self, n, total):
        self.progress.setRange(0,total)
        self.progress.setValue(n)

    def job_finished(self):
        self.centralWidget().setEnabled(True)
        self.progress.setVisible(False)
        self.statusBar().showMessage('Ready. Save a copy to keep your changes.')

    def commit(self, target):
        self.history.append(self.path)
        for path in self.future:
            Path(path).unlink(missing_ok=True)
        self.future.clear()
        # Disk-backed, capped undo history keeps large PDFs out of RAM.
        if len(self.history) > 10:
            Path(self.history.pop(0)).unlink(missing_ok=True)
        self.load(target)
        self.clear_selection()
        self.dirty = True

    def mutate(self, operation, area=False, custom_indices=None, **options):
        indices = self.indices() if custom_indices is None else custom_indices
        selection = self.canvas.selection
        if area and not selection:
            raise ValueError('Draw a rectangle on the page first.')
        if operation == 'text' and not options['text'].strip():
            raise ValueError('Enter some text first.')
        target, source = self.fresh(), self.path
        def task(progress):
            edit(source, target, operation, indices, selection, progress, **options)
            return target
        self.run_job(f'Applying {operation}…', task, self.commit)

    def redact(self):
        self.require()
        if not self.canvas.selection:
            raise ValueError('Draw a rectangle around the content to remove.')
        n = len(self.indices())
        if QMessageBox.question(self, 'Apply real redaction',
            f'Remove content in this area on {n} page(s)? Overlapping vector objects may be removed entirely. '
            'Metadata and embedded files will also be cleared. Review the saved copy before sharing.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.mutate('redact', area=True, black=self.black.isChecked())

    def undo(self):
        if self.history:
            self.future.append(self.path)
            self.load(self.history.pop())
            self.clear_selection()
            self.dirty = True

    def redo(self):
        if self.future:
            self.history.append(self.path)
            self.load(self.future.pop())
            self.clear_selection()
            self.dirty = True

    def output_path(self, title='Save PDF copy'):
        self.require()
        path, _ = QFileDialog.getSaveFileName(self, title, 'edited.pdf', 'PDF files (*.pdf)')
        if path:
            if not path.lower().endswith('.pdf'):
                path += '.pdf'
            if str(Path(path).resolve()) == self.original:
                raise ValueError('Choose a different filename to preserve the original PDF.')
        return path

    def save_pdf(self, password=''):
        path = self.output_path()
        if not path:
            return
        source = self.path
        def task(progress):
            with fitz.open(source) as doc:
                atomic_save(doc, path, password)
            return path
        def done(result):
            self.dirty = False
            QMessageBox.information(self, 'Saved', f'Saved: {result}')
        self.run_job('Saving compressed PDF copy…', task, done)

    def merge(self):
        self.require()
        paths, _ = QFileDialog.getOpenFileNames(self, 'Append PDFs in selected order', '', 'PDF files (*.pdf)')
        if not paths:
            return
        files = []
        for path in paths:
            pw = ''
            with fitz.open(path) as doc:
                if doc.needs_pass:
                    pw, ok = QInputDialog.getText(self, 'Password', Path(path).name, QLineEdit.Password)
                    if not ok:
                        return
                    if not doc.authenticate(pw):
                        raise ValueError('Incorrect password.')
            files.append((path,pw))
        self.mutate('merge', files=files)

    def extra(self, index):
        self.extras.setCurrentIndex(0)
        if not index:
            return
        self.require()
        if index == 1:
            indices = self.indices()
            target = self.output_path('Extract selected pages')
            if target:
                source = self.path
                self.run_job('Extracting…', lambda p: extract(source,target,indices), lambda _: QMessageBox.information(self,'Exported',target))
        elif index == 2:
            spec, ok = QInputDialog.getText(self,'Split PDF','Separate output groups with ; (example: 1-3;4-6;7,9)')
            if not ok:
                return
            groups = [pages_from_text(s,len(self.doc)) for s in spec.split(';')]
            folder = QFileDialog.getExistingDirectory(self,'Choose output parent folder')
            if folder:
                source = self.path
                destination = Path(folder) / ('split_' + uuid.uuid4().hex[:8])
                def task(progress):
                    destination.mkdir()
                    for i, group in enumerate(groups):
                        extract(source,destination/f'part_{i+1:03d}.pdf',group)
                        progress(i+1,len(groups))
                    return str(destination)
                self.run_job('Splitting…',task,lambda p: QMessageBox.information(self,'Split complete',p))
        elif index == 3:
            self.mutate('rotate')
        elif index == 4:
            if QMessageBox.question(self,'Delete pages',f'Delete {len(self.indices())} selected pages?') == QMessageBox.Yes:
                self.mutate('delete')
        elif index == 5:
            spec, ok = QInputDialog.getText(self,'Reorder pages','Complete new order; duplicates allowed (example: 3,1-2,2):')
            if ok:
                self.mutate('reorder',custom_indices=pages_from_text(spec,len(self.doc),unique=False))
        elif index == 6:
            self.mutate('reset_crop')
        elif index in (7,8):
            indices, source = self.indices(), self.path
            folder = QFileDialog.getExistingDirectory(self,'Choose output parent folder')
            if not folder:
                return
            destination = Path(folder)/('export_'+uuid.uuid4().hex[:8])
            def task(progress):
                destination.mkdir()
                with fitz.open(source) as doc:
                    for i,n in enumerate(indices):
                        if index == 7:
                            doc[n].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(str(destination/f'page_{n+1:04d}.png'))
                        else:
                            (destination/f'page_{n+1:04d}.txt').write_text(doc[n].get_text(),encoding='utf-8')
                        progress(i+1,len(indices))
                return str(destination)
            self.run_job('Exporting…',task,lambda p: QMessageBox.information(self,'Exported',p))
        elif index == 9:
            self.mutate('clean')
        elif index == 10:
            pw, ok = QInputDialog.getText(self,'Protect output','New password (keep a safe copy):',QLineEdit.Password)
            if ok and pw:
                self.save_pdf(password=pw)

    def closeEvent(self, event):
        if self.busy():
            QMessageBox.information(self,'Operation in progress','Wait for the current operation to finish.')
            event.ignore()
        elif self.discard_ok():
            if self.doc:
                self.doc.close()
            self.temp.cleanup()
            event.accept()
        else:
            event.ignore()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = Editor()
    window.show()
    sys.exit(app.exec())

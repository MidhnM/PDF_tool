"""Run this file from PyCharm or with: python main.py"""
import os
import sys
import tempfile
import uuid
from pathlib import Path
import pymupdf as fitz
from PySide6.QtCore import Qt, QRectF, Signal, QThread, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QAction, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QFileDialog, QMessageBox, QComboBox,
    QLineEdit, QSpinBox, QDoubleSpinBox, QScrollArea, QSplitter, QFormLayout,
    QGroupBox, QTextEdit, QInputDialog, QProgressBar, QCheckBox, QTabWidget, QDialog, QFrame)
from engine import edit, extract, atomic_save, pages_from_text, apply_overlay
from widgets import Canvas, SavePreviewDialog, page_pixmap


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
        self.pending_save_prompt = False
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(100)
        self.preview_timer.timeout.connect(lambda: self.safe(self.render))
        self.setWindowTitle('PDF Editor Tool | Version 1.0')
        self.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets' / 'app.ico')))
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
        self.button('About', self.about, bar)
        bar.addStretch()
        self.filename = QLabel('Open a PDF to begin')
        bar.addWidget(self.filename)
        splitter = QSplitter()
        outer.addWidget(splitter, 1)
        left = QWidget()
        left.setMinimumWidth(300)
        left.setMaximumWidth(390)
        panel = QVBoxLayout(left)
        intro = QLabel('PDF Editor Tool\nSelect · Edit · Save')
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
        self.mode = QComboBox()
        self.mode.addItems(['Crop', 'Redaction', 'Text', 'Highlight', 'Comment'])
        form.addRow('Preview tool', self.mode)
        hint = QLabel('Drag corners or edges to resize; drag inside to move. Shift+drag draws a new box. Preview the same relative area on any page.')
        hint.setWordWrap(True)
        panel.addWidget(hint)
        self.measure = QLabel('No area selected')
        panel.addWidget(self.measure)
        crop = QGroupBox('Crop')
        crop_layout = QVBoxLayout(crop)
        self.button('Apply crop', lambda: self.mutate('crop', area=True), crop_layout)
        panel.addWidget(crop)
        redact = QGroupBox('Redaction · removes content')
        redact_layout = QVBoxLayout(redact)
        self.black = QCheckBox('Black fill (otherwise white)')
        redact_layout.addWidget(self.black)
        self.button('Redact selected area', self.redact, redact_layout)
        panel.addWidget(redact)
        annotations = QGroupBox('Text && comments')
        annotation_layout = QVBoxLayout(annotations)
        self.tabs = QTabWidget()
        annotation_layout.addWidget(self.tabs)
        text_page = QWidget()
        tf = QVBoxLayout(text_page)
        self.text = QTextEdit('Page {page} of {pages}')
        self.text.setFixedHeight(70)
        tf.addWidget(self.text)
        self.font_size = QSpinBox()
        self.font_size.setRange(6,144)
        self.font_size.setValue(12)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel('Maximum size (pt)'))
        size_row.addWidget(self.font_size)
        tf.addLayout(size_row)
        self.autofit = QCheckBox('Auto-fit text when box changes')
        self.autofit.setChecked(True)
        tf.addWidget(self.autofit)
        self.button('Add text',lambda: self.mutate('text',area=True,**self.overlay_options('text')),tf)
        self.tabs.addTab(text_page,'Text')
        highlight_page = QWidget()
        hl = QVBoxLayout(highlight_page)
        self.highlight_color = QComboBox()
        for name,color in [('Yellow',(1,.84,0)),('Green',(.2,.85,.4)),('Blue',(.25,.65,1)),
                           ('Pink',(1,.35,.65)),('Orange',(1,.6,.1))]:
            self.highlight_color.addItem(name,color)
        hl.addWidget(self.highlight_color)
        self.highlight_area = QCheckBox('Area highlight (for scans / images)')
        hl.addWidget(self.highlight_area)
        self.highlight_note = QTextEdit()
        self.highlight_note.setPlaceholderText('Optional comment attached to highlight')
        self.highlight_note.setFixedHeight(65)
        hl.addWidget(self.highlight_note)
        self.button('Add highlight',lambda: self.mutate('highlight',area=True,**self.overlay_options('highlight')),hl)
        self.tabs.addTab(highlight_page,'Highlight')
        comment_page = QWidget()
        cl = QVBoxLayout(comment_page)
        self.comment = QTextEdit()
        self.comment.setPlaceholderText('Add a sticky-note comment to this location')
        self.comment.setFixedHeight(85)
        cl.addWidget(self.comment)
        self.button('Add comment',lambda: self.mutate('comment',area=True,**self.overlay_options('comment')),cl)
        self.button('View comments on current page', self.view_comments, cl)
        self.tabs.addTab(comment_page,'Comment')
        panel.addWidget(annotations)
        self.overlay_status = QLabel('')
        self.overlay_status.setWordWrap(True)
        panel.addWidget(self.overlay_status)
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
        sidebar = QScrollArea()
        sidebar.setWidgetResizable(True)
        sidebar.setMinimumWidth(335)
        sidebar.setMaximumWidth(415)
        sidebar.setFrameShape(QFrame.NoFrame)
        sidebar.setWidget(left)
        splitter.addWidget(sidebar)
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
        watermark = QLabel('Created by Midhun M')
        watermark.setStyleSheet('color:rgba(50,65,85,65);font-size:12px;padding:4px 12px;')
        self.statusBar().addPermanentWidget(watermark)
        self.mode.currentTextChanged.connect(self.mode_changed)
        self.tabs.currentChanged.connect(lambda i: self.mode.setCurrentText(['Text','Highlight','Comment'][i]))
        self.text.textChanged.connect(lambda: self.activate_overlay('Text'))
        self.font_size.valueChanged.connect(lambda: self.activate_overlay('Text'))
        self.autofit.toggled.connect(lambda: self.activate_overlay('Text'))
        self.highlight_color.currentIndexChanged.connect(lambda: self.activate_overlay('Highlight'))
        self.highlight_area.toggled.connect(lambda: self.activate_overlay('Highlight'))
        self.highlight_note.textChanged.connect(lambda: self.activate_overlay('Highlight'))
        self.comment.textChanged.connect(lambda: self.activate_overlay('Comment'))
        for shortcut, fn in [('Ctrl+O',self.open_pdf), ('Ctrl+S',self.save_pdf), ('Ctrl+Z',self.undo), ('Ctrl+Y',self.redo)]:
            action = QAction(self)
            action.setShortcut(shortcut)
            action.triggered.connect(lambda checked=False, f=fn: self.safe(f) if not self.busy() else None)
            self.addAction(action)
        self.setStyleSheet('QMainWindow {background:#f3f5f8;} QPushButton {padding:7px;} '
            'QGroupBox {background:#ffffff;border:1px solid #d1d9e4;border-radius:6px;'
            'margin-top:12px;padding-top:14px;font-weight:600;} '
            'QGroupBox::title {subcontrol-origin:margin;left:12px;padding:0 4px;} '
            'QLineEdit,QComboBox,QSpinBox {padding:4px;}')

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

    def about(self):
        QMessageBox.about(self, 'About PDF Editor Tool',
            '<h2>PDF Editor Tool</h2><p>Version: <b>1.0</b></p>'
            '<p>Creator: <b>Midhun M</b></p>'
            '<p>Crop, organize, annotate and redact PDFs locally.</p>')

    def view_comments(self):
        self.require()
        page = self.doc[self.page.value()-1]
        notes=[]
        for annot in page.annots() or []:
            info=annot.info
            if info.get('content'):
                notes.append(f"{annot.type[1]} — {info.get('title') or 'Unknown author'}\n{info['content']}")
        dialog=QDialog(self)
        dialog.setWindowTitle(f'Comments — page {self.page.value()}')
        dialog.resize(520,400)
        layout=QVBoxLayout(dialog)
        content=QTextEdit()
        content.setReadOnly(True)
        content.setPlainText('\n\n'.join(notes) or 'No comments on this page.')
        layout.addWidget(content)
        close=QPushButton('Close')
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def mode_changed(self, mode):
        self.canvas.mode = mode
        if mode in ('Text','Highlight','Comment'):
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(['Text','Highlight','Comment'].index(mode))
            self.tabs.blockSignals(False)
        self.canvas.update()
        self.preview_timer.start()

    def activate_overlay(self, mode):
        self.mode.setCurrentText(mode)
        self.preview_timer.start()

    def overlay_options(self, operation):
        if operation == 'text':
            return dict(text=self.text.toPlainText(),size=self.font_size.value(),autofit=self.autofit.isChecked())
        if operation == 'highlight':
            return dict(color=self.highlight_color.currentData(),note=self.highlight_note.toPlainText(),
                        area_highlight=self.highlight_area.isChecked())
        return dict(note=self.comment.toPlainText(),color=(1,.84,0))

    def render(self):
        if not self.doc or self.busy():
            return
        index = self.page.value()-1
        page = self.doc[index]
        scale = min(self.zoom.value(),2600/max(page.rect.width,page.rect.height))
        operation = self.mode.currentText().lower()
        self.overlay_status.setText('')
        if self.canvas.selection and operation in ('text','highlight','comment'):
            # The live overlay is a disposable PDF page. Original content is never changed by preview.
            with fitz.open() as preview:
                preview.insert_pdf(self.doc,from_page=index,to_page=index)
                try:
                    message = apply_overlay(preview[0],operation,self.canvas.selection,index+1,len(self.doc),
                                            **self.overlay_options(operation))
                    self.overlay_status.setText(message + ' · not applied yet')
                except ValueError as exc:
                    self.overlay_status.setText(str(exc))
                page = preview.reload_page(preview[0])
                self.canvas.set_page(page_pixmap(page,scale))
        else:
            self.canvas.set_page(page_pixmap(page,scale))
        self.update_measure()

    def update_measure(self):
        selection = self.canvas.selection
        if not selection or not self.doc:
            self.measure.setText('No area selected')
            return
        x0,y0,x1,y1 = selection
        page = self.doc[self.page.value()-1]
        self.measure.setText(f'Box: {(x1-x0)*page.rect.width:.1f} × {(y1-y0)*page.rect.height:.1f} pt')

    def selection_changed(self, selection):
        self.update_measure()
        self.preview_timer.start()

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
        self.preview_timer.stop()
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
        self.render()
        self.statusBar().showMessage('Ready. Save a copy to keep your changes.')
        if self.pending_save_prompt:
            self.pending_save_prompt = False
            QTimer.singleShot(0,lambda: self.safe(self.save_pdf))

    def commit(self, target):
        self.history.append(self.path)
        for path in self.future:
            Path(path).unlink(missing_ok=True)
        self.future.clear()
        # Disk-backed, capped undo history keeps large PDFs out of RAM.
        if len(self.history) > 10:
            Path(self.history.pop(0)).unlink(missing_ok=True)
        self.clear_selection()
        self.load(target)
        self.dirty = True
        self.pending_save_prompt = True

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
        self.require()
        dialog = SavePreviewDialog(self.doc,self.page.value()-1,self)
        if dialog.exec() != QDialog.Accepted:
            return
        indices = list(dialog.output_indices)
        path = self.output_path()
        if not path:
            return
        source = self.path
        complete = indices == list(range(len(self.doc)))
        def task(progress):
            with fitz.open(source) as doc:
                if not complete:
                    doc.select(indices)
                atomic_save(doc,path,password)
            return path
        def done(result):
            # A subset export does not save all remaining document edits.
            if complete:
                self.dirty = False
            QMessageBox.information(self,'Saved',f'Saved {len(indices)} page(s): {result}')
        self.run_job('Saving PDF copy…',task,done)

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
            self.preview_timer.stop()
            if self.doc:
                self.doc.close()
                self.doc = None
            self.temp.cleanup()
            event.accept()
        else:
            event.ignore()


if __name__ == '__main__':
    # Give Windows a stable app identity instead of grouping this under python.exe.
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('MidhunM.PDFEditorTool.1.0')
    app = QApplication(sys.argv)
    app.setApplicationName('PDF Editor Tool')
    app.setApplicationVersion('1.0')
    app.setOrganizationName('Midhun M')
    app.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets' / 'app.ico')))
    app.setStyle('Fusion')
    window = Editor()
    window.show()
    sys.exit(app.exec())

"""Reusable selection canvas and save-preview dialog."""
from PySide6.QtCore import Qt, QRectF, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QImage
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QLineEdit, QSpinBox, QPushButton, QDialogButtonBox)
import pymupdf as fitz
from engine import pages_from_text


def page_pixmap(page, scale):
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
    return QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride,
                                   QImage.Format_RGB888).copy())


class Canvas(QWidget):
    selected = Signal(object)

    def __init__(self):
        super().__init__()
        self.pix = QPixmap()
        self.selection = None
        self.anchor = None
        self.start_box = None
        self.drag_mode = None
        self.mode = 'Crop'
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setMinimumSize(1, 1)

    def set_page(self, pix):
        self.pix = pix
        self.setFixedSize(pix.size())
        self.update()

    def fraction(self, p):
        return (max(0., min(1., p.x()/self.width())), max(0., min(1., p.y()/self.height())))

    def box(self):
        a,b,c,d = self.selection
        return QRectF(a*self.width(), b*self.height(), (c-a)*self.width(), (d-b)*self.height())

    def hit(self, point):
        if not self.selection:
            return 'new'
        r = self.box()
        x,y = point.x(),point.y()
        left,right = abs(x-r.left())<=8,abs(x-r.right())<=8
        top,bottom = abs(y-r.top())<=8,abs(y-r.bottom())<=8
        if r.adjusted(-8,-8,8,8).contains(point):
            edge = ('n' if top else 's' if bottom else '') + ('w' if left else 'e' if right else '')
            if edge:
                return edge
        return 'move' if r.contains(point) else 'new'

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.pix.isNull():
            self.anchor = self.fraction(event.position())
            self.start_box = self.selection
            self.drag_mode = 'new' if event.modifiers() & Qt.ShiftModifier else self.hit(event.position())
            if self.drag_mode == 'new':
                self.selection = None
            self.update()

    def mouseMoveEvent(self, event):
        if self.anchor is None:
            hit = self.hit(event.position())
            cursor = {'move':Qt.SizeAllCursor, 'n':Qt.SizeVerCursor, 's':Qt.SizeVerCursor,
                      'w':Qt.SizeHorCursor, 'e':Qt.SizeHorCursor, 'nw':Qt.SizeFDiagCursor,
                      'se':Qt.SizeFDiagCursor, 'ne':Qt.SizeBDiagCursor,'sw':Qt.SizeBDiagCursor}
            self.setCursor(cursor.get(hit,Qt.CrossCursor))
            return
        x,y = self.fraction(event.position())
        ax,ay = self.anchor
        mode = self.drag_mode
        if mode == 'new':
            self.selection = (min(ax,x), min(ay,y), max(ax,x), max(ay,y))
        elif mode == 'move':
            l,t,r,b = self.start_box
            dx = max(-l,min(1-r,x-ax))
            dy = max(-t,min(1-b,y-ay))
            self.selection = (l+dx,t+dy,r+dx,b+dy)
        else:
            l,t,r,b = self.start_box
            minimum_x, minimum_y = 4/self.width(),4/self.height()
            if 'w' in mode: l=min(x,r-minimum_x)
            if 'e' in mode: r=max(x,l+minimum_x)
            if 'n' in mode: t=min(y,b-minimum_y)
            if 's' in mode: b=max(y,t+minimum_y)
            self.selection=(l,t,r,b)
        self.update()
        self.selected.emit(self.selection)

    def mouseReleaseEvent(self, event):
        if self.anchor is not None and event.button() == Qt.LeftButton:
            self.mouseMoveEvent(event)
            self.anchor = None
            if self.selection and (self.selection[2]-self.selection[0] < .003 or self.selection[3]-self.selection[1] < .003):
                self.selection = None
            self.selected.emit(self.selection)
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('white'))
        p.drawPixmap(0,0,self.pix)
        if self.selection:
            r=self.box()
            if self.mode == 'Crop':
                shade=QColor(16,24,40,125)
                for rect in (QRectF(0,0,self.width(),r.top()),
                    QRectF(0,r.bottom(),self.width(),self.height()-r.bottom()),
                    QRectF(0,r.top(),r.left(),r.height()),
                    QRectF(r.right(),r.top(),self.width()-r.right(),r.height())):
                    p.fillRect(rect,shade)
            elif self.mode == 'Redaction':
                p.fillRect(r,QColor(239,68,68,80))
            p.setPen(QPen(QColor('#2563eb'),1.5))
            p.drawRect(r)
            p.setBrush(QColor('white'))
            for x,y in ((r.left(),r.top()),(r.center().x(),r.top()),(r.right(),r.top()),
                        (r.left(),r.center().y()),(r.right(),r.center().y()),
                        (r.left(),r.bottom()),(r.center().x(),r.bottom()),(r.right(),r.bottom())):
                p.drawRect(QRectF(x-4,y-4,8,8))


class SavePreviewDialog(QDialog):
    """Output scope is independent of edit scope. Only one output page is rendered."""
    def __init__(self, doc, current, parent=None):
        super().__init__(parent)
        self.doc,self.current=doc,current
        self.output_indices=[]
        self.setWindowTitle('Save edited PDF — preview pages')
        self.resize(680,760)
        layout=QVBoxLayout(self)
        title=QLabel('Choose which pages to save')
        title.setStyleSheet('font-size:19px;font-weight:600')
        layout.addWidget(title)
        row=QHBoxLayout()
        self.scope=QComboBox()
        self.scope.addItems(['All pages','Current page','Page range'])
        self.ranges=QLineEdit(str(current+1))
        self.ranges.setPlaceholderText('1-3,5,8-10')
        row.addWidget(self.scope)
        row.addWidget(self.ranges)
        layout.addLayout(row)
        self.count=QLabel()
        layout.addWidget(self.count)
        self.preview=QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(250,300)
        self.preview.setStyleSheet('background:#dfe5ed; border:1px solid #cbd5e1;')
        layout.addWidget(self.preview,1)
        nav=QHBoxLayout()
        previous=QPushButton('Previous')
        following=QPushButton('Next')
        self.page=QSpinBox()
        nav.addWidget(previous)
        nav.addWidget(QLabel('Output page'))
        nav.addWidget(self.page)
        nav.addWidget(following)
        layout.addLayout(nav)
        buttons=QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Cancel).setText('Keep editing')
        self.save_button=buttons.button(QDialogButtonBox.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        previous.clicked.connect(lambda: self.page.setValue(self.page.value()-1))
        following.clicked.connect(lambda: self.page.setValue(self.page.value()+1))
        self.scope.currentIndexChanged.connect(self.refresh_scope)
        self.ranges.textChanged.connect(self.refresh_scope)
        self.page.valueChanged.connect(self.render)
        self.refresh_scope()

    def refresh_scope(self):
        mode=self.scope.currentIndex()
        self.ranges.setEnabled(mode==2)
        try:
            self.output_indices=(list(range(len(self.doc))) if mode==0 else [self.current] if mode==1
                                 else pages_from_text(self.ranges.text(),len(self.doc)))
            self.page.blockSignals(True)
            self.page.setRange(1,len(self.output_indices))
            self.page.setValue(1)
            self.page.blockSignals(False)
            self.save_button.setEnabled(True)
            self.render()
        except ValueError as exc:
            self.output_indices=[]
            self.save_button.setEnabled(False)
            self.preview.clear()
            self.count.setText(str(exc))

    def render(self):
        if not self.output_indices:
            return
        n=self.output_indices[self.page.value()-1]
        page=self.doc[n]
        scale=min(max(1,self.preview.width()-20)/page.rect.width,
                  max(1,self.preview.height()-20)/page.rect.height,2)
        self.preview.setPixmap(page_pixmap(page,scale))
        self.count.setText(f'{len(self.output_indices)} page(s) to save · Preview: document page {n+1}')

    def resizeEvent(self,event):
        super().resizeEvent(event)
        QTimer.singleShot(0,self.render)

"""PDF operations, independent of the GUI. Coordinates are visible-page fractions."""
from pathlib import Path
import os
import tempfile
import pymupdf as fitz


def pages_from_text(text, count, unique=True):
    result = []
    for part in text.replace(' ', '').split(','):
        if not part:
            raise ValueError('Enter pages such as 1-3,5,8-10.')
        nums = part.split('-')
        if len(nums) == 1:
            values = [int(nums[0])]
        elif len(nums) == 2:
            start, end = map(int, nums)
            values = range(start, end + (1 if end >= start else -1), 1 if end >= start else -1)
        else:
            raise ValueError('Invalid page range.')
        for n in values:
            if not 1 <= n <= count:
                raise ValueError(f'Page {n} is outside 1-{count}.')
            if not unique or n - 1 not in result:
                result.append(n - 1)
    return result


def visible_rect(page, selection):
    w, h = page.rect.width, page.rect.height
    return fitz.Rect(selection[0]*w, selection[1]*h, selection[2]*w, selection[3]*h)


def content_rect(page, selection):
    return visible_rect(page, selection) * page.derotation_matrix


def atomic_save(doc, path, password=''):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(suffix='.pdf', dir=path.parent)
    os.close(fd)
    try:
        options = dict(garbage=4, deflate=True, clean=True)
        if password:
            options.update(encryption=fitz.PDF_ENCRYPT_AES_256,
                           owner_pw=password, user_pw=password)
        doc.save(tmp, **options)
        with fitz.open(tmp) as check:
            if check.needs_pass:
                check.authenticate(password)
            if not check.page_count:
                raise ValueError('Output has no pages.')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def edit(source, target, operation, indices, selection=None, progress=None, **options):
    with fitz.open(source) as doc:
        if operation == 'merge':
            for path, password in options['files']:
                with fitz.open(path) as other:
                    if other.needs_pass and not other.authenticate(password):
                        raise ValueError(f'Incorrect password: {Path(path).name}')
                    doc.insert_pdf(other)
        elif operation == 'reorder':
            doc.select(indices)
        elif operation == 'delete':
            if len(set(indices)) >= len(doc):
                raise ValueError('At least one page must remain.')
            doc.delete_pages(sorted(set(indices)))
        else:
            for done, index in enumerate(indices):
                page = doc[index]
                if operation == 'crop':
                    r = content_rect(page, selection)
                    r += (page.cropbox_position.x, page.cropbox_position.y,
                          page.cropbox_position.x, page.cropbox_position.y)
                    page.set_cropbox(r)
                elif operation == 'redact':
                    r = content_rect(page, selection)
                    # Remove overlapping annotations and fields as well as page content.
                    for annot in list(page.annots() or []):
                        if annot.rect.intersects(r):
                            page.delete_annot(annot)
                    for widget in list(page.widgets() or []):
                        if widget.rect.intersects(r):
                            page.delete_widget(widget)
                    page.add_redact_annot(r, fill=(0, 0, 0) if options.get('black') else (1, 1, 1))
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS,
                        graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                        text=fitz.PDF_REDACT_TEXT_REMOVE)
                elif operation in ('text', 'highlight', 'comment'):
                    apply_overlay(page, operation, selection, index+1, len(doc), **options)
                elif operation == 'rotate':
                    page.set_rotation((page.rotation + options.get('angle', 90)) % 360)
                elif operation == 'reset_crop':
                    page.set_cropbox(page.mediabox)
                elif operation == 'clean':
                    pass
                else:
                    raise ValueError(f'Unknown operation: {operation}')
                if progress:
                    progress(done+1, len(indices))
        if operation in ('redact', 'clean'):
            doc.set_metadata({})
            doc.del_xml_metadata()
            for name in doc.embfile_names():
                doc.embfile_del(name)
        atomic_save(doc, target)


def extract(source, target, indices):
    with fitz.open(source) as doc:
        doc.select(indices)
        atomic_save(doc, target)


def apply_overlay(page, operation, selection, page_number, page_count, **options):
    """Shared by preview and export: identical PDF layout rather than a Qt approximation."""
    r = content_rect(page, selection)
    if operation == 'text':
        text = options['text'].replace('{page}', str(page_number)).replace('{pages}', str(page_count))
        if any(ord(c) > 255 for c in text):
            raise ValueError('Built-in font supports Latin text. Use Latin characters for this version.')
        if not text.strip():
            raise ValueError('Enter text to preview.')
        requested = float(options['size'])
        sizes = [requested]
        if options.get('autofit', False):
            sizes.extend(v/2 for v in range(int(requested*2)-1, 7, -1))
        for size in sizes:
            vr = visible_rect(page, selection)
            padding = size * .2
            vr += (padding, padding, -padding, -padding)
            if vr.is_empty:
                continue
            shape = page.new_shape()
            remaining = shape.insert_textbox(vr * page.derotation_matrix, text,
                fontsize=size, fontname='helv', color=options.get('color',(0,0,0)),
                rotate=page.rotation)
            if remaining >= 0:
                shape.commit()
                return f'Text preview · {size:g} pt'
        raise ValueError(f'Text does not fit on page {page_number}. Enlarge the box or reduce text.')
    color = options.get('color',(1, .84, 0))
    note = options.get('note','')
    if operation == 'highlight':
        if options.get('area_highlight',False):
            annot=page.add_rect_annot(r)
            annot.set_colors(stroke=color,fill=color)
            annot.set_border(width=0)
        else:
            # Recover character quads so angled text is highlighted in its own direction.
            quads=[]
            for block in page.get_text('rawdict')['blocks']:
                for line in block.get('lines',[]):
                    for span in line['spans']:
                        run=[]
                        for char in span['chars']:
                            if r.intersects(fitz.Rect(char['bbox'])):
                                run.append(char)
                            elif run:
                                quads.append(fitz.recover_span_quad(line['dir'],span,chars=run))
                                run=[]
                        if run:
                            quads.append(fitz.recover_span_quad(line['dir'],span,chars=run))
            if not quads:
                raise ValueError(f'No selectable text on page {page_number}. Choose Area highlight for scans.')
            annot=page.add_highlight_annot(quads)
            annot.set_colors(stroke=color)
        annot.set_opacity(.35)
        annot.set_info(title='Midhun M',content=note)
        annot.update()
        return 'Highlight preview'
    if operation == 'comment':
        if not note.strip():
            raise ValueError('Enter a comment first.')
        point = visible_rect(page,selection).tl * page.derotation_matrix
        annot=page.add_text_annot(point,note,icon='Comment')
        annot.set_colors(stroke=color)
        annot.set_info(title='Midhun M',content=note)
        annot.update()
        return 'Comment preview · note appears as a PDF comment icon'
    raise ValueError('Unknown overlay type.')

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
                elif operation == 'text':
                    vr = visible_rect(page, selection)
                    padding = options['size'] * 0.2
                    vr += (padding, padding, -padding, -padding)
                    if vr.is_empty:
                        raise ValueError('Text box is too small.')
                    r = vr * page.derotation_matrix
                    text = options['text'].replace('{page}', str(index+1)).replace('{pages}', str(len(doc)))
                    if any(ord(c) > 255 for c in text):
                        raise ValueError('Built-in font supports Latin text. Use Latin characters for this version.')
                    remaining = page.insert_textbox(r, text, fontsize=options['size'],
                        fontname='helv', color=options.get('color', (0, 0, 0)), rotate=page.rotation)
                    if remaining < 0:
                        raise ValueError(f'Text does not fit on page {index+1}. Draw a larger box or reduce font size.')
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

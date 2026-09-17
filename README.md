# PDF Desk - Python desktop PDF editor

A local desktop application built with PySide6 and PyMuPDF. Open the folder in PyCharm and run `main.py`.

## Setup in PyCharm (Windows, macOS, or Linux)

1. Install Python 3.11 or 3.12 (64-bit recommended).
2. Extract this ZIP into a folder you can write to.
3. In PyCharm choose **Open**, then select the `pdf_desktop_editor` folder.
4. Configure a project virtual environment with Python 3.11 or 3.12.
5. In the PyCharm terminal run:

   ```sh
   python -m pip install -r requirements.txt
   ```

6. Right-click `main.py` and select **Run 'main'**. Alternatively:

   ```sh
   python main.py
   ```

No API keys, server, account, or Internet connection are needed after dependency installation. If the imports fail, check that the terminal and PyCharm run configuration use the same interpreter. Install **PyMuPDF**, not the unrelated package named `fitz`.

## Daily workflow

- **Open PDF**: opens a working copy. Password-protected documents prompt for a password.
- Choose **All pages**, **Current page**, or **Page range**. Page ranges use 1-based numbers: `1-3,5,8-10`.
- Drag a rectangle on the page. Outside the rectangle is shaded and the crop preview updates during dragging. Draw again to replace the rectangle. Change pages or zoom to review the same selection.
- **Apply crop** keeps the selected visible rectangle across the chosen pages. This is an editable crop, not content destruction; use redaction to remove confidential content.
- **Redact selected area** removes content under the rectangle, with white or black fill. This works on the current page, selected ranges, or all pages. Save a new copy and inspect it before sharing.
- **Add text** puts text in the selected rectangle on the chosen pages. Use `{page}` for the actual document page number and `{pages}` for the total. Text runs horizontally as viewed, including rotated pages. Text is left aligned using Helvetica; this version supports Latin characters. If the text cannot fit, the entire operation is rejected: enlarge the rectangle or reduce the font size.
- **Save copy** writes a full, compressed PDF to a separate filename. It does not overwrite the originally opened file.

Selection coordinates are proportional to the currently visible page, not fixed millimetre offsets. For example, selecting the top 10% on an A4 page selects the top 10% on an A3 page. The same box remains selected while browsing pages; it is cleared after an edit. This also handles mixed page rotations and existing crop boxes.

## More tools

| Tool | Behaviour |
| --- | --- |
| Append PDFs | Appends one or more files to the open PDF. For precise ordering, append one at a time or use Reorder afterward. |
| Extract selected pages | Exports the selected scope as a new PDF. |
| Split by page groups | `1-3;4-6;7,9` creates three PDFs in a new output subfolder. Unlisted pages are omitted from these exports; the original stays intact. |
| Rotate | Rotates chosen pages 90 degrees clockwise. |
| Delete pages | Deletes chosen pages; at least one must remain. |
| Reorder / duplicate | Enter the complete output order, e.g. `3,1-2,2`. Unlisted pages are omitted from the working copy. |
| Reset crops | Restores the selected pages to their media boxes; cannot restore redacted content. |
| Export PNG | Exports chosen pages at 144 DPI into a new subfolder. |
| Export text | Writes one UTF-8 text file per selected page. Scans need external OCR first. |
| Clean metadata / attachments | Clears standard metadata, XML metadata, and embedded files throughout the document. |
| Password-protected copy | Saves with AES-256 encryption and a password required to open. |
| Undo / Redo | Up to 10 disk-backed edit snapshots per open document. |

Keyboard shortcuts: Ctrl+O open, Ctrl+S save copy, Ctrl+Z undo, Ctrl+Y redo.

## Redaction behaviour and scope

Redaction calls `Page.apply_redactions()` with text removal, overlapping image-pixel removal, and removal of touched vector graphics. Overlapping annotations and form fields are removed too. The full rewritten save uses garbage collection; it does not append an incremental revision containing the old page content. Metadata and embedded files are cleared for every redaction operation.

A vector object crossing the rectangle can be removed in its entirety. Redacting one page does not remove copies of the same text or shared image used on other pages. The original input PDF remains unchanged. Undo snapshots containing earlier content exist in the system temporary directory during the session and are normally deleted on exit; an abnormal process termination can leave temporary files behind. These snapshots are not embedded in exported PDFs.

This is region-based redaction, not a complete forensic sanitization tool. Data elsewhere in a document, such as bookmarks, scripts, optional layers, nonoverlapping annotations, or repeated content, is outside that region operation. Review your chosen page scope and saved output for sensitive use.

## Performance and limitations

- No programmed page-count limit. The preview renders only one page, capped at 2600 pixels on its longest side. Batch edits and exports use a background worker with progress; loading and preview rendering are synchronous.
- Very large PDFs still require time, memory, and temporary disk space. Up to 10 undo snapshots may consume many times the original file size. There is no batch cancellation in this version.
- Existing PDF text cannot be edited like a Word document. Use redaction followed by added text for simple replacements.
- No OCR, digital signing, arbitrary Unicode font picker, image insertion, or interactive form editor in this version.
- Edits can invalidate digital signatures. Complex forms, bookmarks, and cross-document links should be checked after merging, extracting, deleting, or reordering pages.
- Compression is lossless stream/object cleanup, not image downsampling; some outputs can be larger.
- Automated checks were run on Linux with an offscreen Qt display. Native Windows/macOS behaviour should be verified on your computer.

## Project files

- `main.py`: desktop UI, live selection, background jobs, history, file dialogs.
- `engine.py`: page parsing, crop/text/redaction transformations, merging, extraction, atomic saves.
- `tests/`: PDF-content and GUI interaction tests.
- `requirements.txt`: exact dependency versions used for verification.
- `preview.png`: example application screenshot.

Run the checks from the project folder:

```sh
python -m unittest discover -s tests -v
```

The suite checks a 301-page crop, rotation and crop offsets, text placement, real text/image redaction, merge/extraction/reordering/deletion, password encryption, error rollback, live selection, background edits, and undo/redo.

Technical references: [PyMuPDF page operations](https://pymupdf.readthedocs.io/en/latest/page.html), [PySide6](https://doc.qt.io/qtforpython-6/). Third-party dependencies retain their own licenses; see [PyMuPDF licensing](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright) and [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html) before distributing an application.

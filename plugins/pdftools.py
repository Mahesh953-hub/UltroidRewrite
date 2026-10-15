# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}pdf <page num> <reply to pdf file>`
    Extract & send page(s) as image(s).
    (note-: For extracting all pages, just use .pdf)
    to upload selected range `{i}pdf 1-7`

• `{i}pdtext <page num> <reply to pdf file>`
    Extract Text From the Pdf.
    (note-: For Extraction all text just use .pdtext)
    to extract selected pages `{i}pdtext 1-7`

• `{i}pdocr [-r <range>] [-o <name>] <reply to pdf file>`
    OCR-extract text from a PDF (renders pages to images and runs
    tesseract). Falls back to embedded text extraction when no OCR
    engine is installed.
    `-r` selects pages, e.g. `1-5,7,10-12` (default: all pages).
    `-o` sets a custom output file name.

• `{i}pdsplit [-r <range>] [-o <name>] <reply to pdf file>`
    Split a PDF into separate files.
    With `-r` each comma part becomes one file, e.g. `1-5,7,10-12`
    produces three PDFs (pages 1-5, page 7, pages 10-12).
    Without `-r` every page becomes its own file.
    `-o` sets a custom base name for the output files.

• `{i}pdmerge [-o <name>] <reply to pdf/album>`
    Merge multiple PDFs into one. Reply to a single PDF or to any
    message in an album of PDFs to merge the whole group.
    `-o` sets a custom output file name.

• `{i}pdscan <reply to image>`
    It scan, crop & send image(s) as pdf.

• `{i}pdsave <reply to image/pdf>`
    It scan, crop & save file to merge.
    you can merge many pages in a single pdf.

• `{i}pdsend [-o <name>]`
    Merge & send the pdf, collected from .pdsave.
    `-o` sets a custom output file name.
"""

import glob
import os
import shlex
import shutil
import time

import cv2
import numpy as np

from . import (
    HNDLR,
    LOGS,
    eor,
    get_string,
    ultroid_cmd,
)

try:
    from PIL import Image
except ImportError:
    Image = None
    LOGS.info(f"{__file__}: PIL not Installed.")

# --- PDF library compatibility shim -------------------------------------------
# Supports pypdf (modern), PyPDF2>=3 (new names) and legacy PyPDF2<3 (old names).
try:
    from pypdf import PdfReader, PdfWriter

    _PDF_NEW = True
except ImportError:
    try:
        from PyPDF2 import PdfReader, PdfWriter

        _PDF_NEW = True
    except ImportError:
        try:
            from PyPDF2 import PdfFileReader, PdfFileWriter

            PdfReader = PdfFileReader
            PdfWriter = PdfFileWriter
            _PDF_NEW = False
        except ImportError:
            PdfReader = PdfWriter = None
            _PDF_NEW = False
            LOGS.info(f"{__file__}: No PDF library (pypdf/PyPDF2) installed.")

# --- OCR engine imports (all optional) ----------------------------------------
try:
    import pytesseract

    _HAVE_TESSERACT = True
except ImportError:
    pytesseract = None
    _HAVE_TESSERACT = False

try:
    from pdf2image import convert_from_path

    _HAVE_PDF2IMAGE = True
except ImportError:
    convert_from_path = None
    _HAVE_PDF2IMAGE = False

try:
    import fitz  # PyMuPDF

    _HAVE_FITZ = True
except ImportError:
    fitz = None
    _HAVE_FITZ = False

# A page can be rendered to a real image when PyMuPDF (+PIL) or pdf2image exists.
_CAN_RENDER = (_HAVE_FITZ and Image is not None) or _HAVE_PDF2IMAGE
# Real OCR is possible only with tesseract plus a way to render pages.
_CAN_OCR = _HAVE_TESSERACT and _CAN_RENDER

from telethon.errors.rpcerrorlist import PhotoSaveFileInvalidError

from pyUltroid.fns import fns

if not os.path.isdir("pdf"):
    os.mkdir("pdf")

# Soft caps to avoid flooding a chat with hundreds of files / slow OCR runs.
_MAX_IMG_PAGES = 50
_MAX_OCR_PAGES = 50
_MAX_SPLIT_PARTS = 30


# --------------------------------------------------------------------------- #
#  Helpers (PDF library agnostic + validation + arg parsing + progress)
# --------------------------------------------------------------------------- #
def _have_pdf_lib():
    return PdfReader is not None


def _num_pages(pdf):
    try:
        return len(pdf.pages)
    except AttributeError:
        return pdf.numPages


def _get_page(pdf, idx):
    try:
        return pdf.pages[idx]
    except AttributeError:
        return pdf.getPage(idx)


def _add_page(writer, page):
    try:
        writer.add_page(page)
    except AttributeError:
        writer.addPage(page)


def _extract_text(page):
    try:
        return page.extract_text() or ""
    except AttributeError:
        try:
            return page.extractText() or ""
        except Exception:
            return ""


def _is_encrypted(pdf):
    try:
        return bool(pdf.is_encrypted)
    except AttributeError:
        try:
            return bool(pdf.isEncrypted)
        except Exception:
            return False


def _try_decrypt(pdf, password=""):
    if not _is_encrypted(pdf):
        return True
    try:
        return pdf.decrypt(password) > 0
    except Exception:
        return False


def _close_reader(reader):
    try:
        reader.close()
    except Exception:
        pass


def _close_writer(writer):
    try:
        writer.close()
    except Exception:
        pass


def _safe_open(path):
    """Open a PDF reader with validation.

    Returns ``(reader, num_pages, error_message)``. ``error_message`` is a
    ready-to-send string when the file is corrupted, password-protected or
    empty, otherwise ``None``.
    """
    if not _have_pdf_lib():
        return None, 0, "`No PDF library installed (need pypdf/PyPDF2).`"
    try:
        reader = PdfReader(path)
    except Exception as e:
        return None, 0, f"`Failed to read PDF (possibly corrupted): {e}`"
    if _is_encrypted(reader) and not _try_decrypt(reader, ""):
        return None, 0, "`This PDF is password-protected. Decrypt it first.`"
    n = _num_pages(reader)
    if n == 0:
        return None, 0, "`This PDF has no pages (empty PDF).`"
    return reader, n, None


def _cleanup(path):
    try:
        if path and os.path.exists(path):
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
    except Exception:
        pass


def _sanitize_name(name):
    """Return a safe base file name (no path, no extension, no slashes)."""
    name = os.path.basename(str(name))
    name = name.replace("/", "_").replace("\\", "_")
    root, _ext = os.path.splitext(name)
    return root or "pdf"


def _parse_flags(text):
    """Extract ``-o/--output <name>`` and ``-r/--range <range>`` flags.

    Returns a dict with ``output``, ``ranges`` and ``rest`` (remaining text).
    """
    out = {"output": None, "ranges": None, "rest": ""}
    if not text:
        return out
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    rest = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-o", "--output") and i + 1 < len(tokens):
            out["output"] = tokens[i + 1]
            i += 2
        elif t in ("-r", "--range") and i + 1 < len(tokens):
            out["ranges"] = tokens[i + 1]
            i += 2
        elif t.startswith("-o") and len(t) > 2:
            out["output"] = t[2:]
            i += 1
        elif t.startswith("-r") and len(t) > 2:
            out["ranges"] = t[2:]
            i += 1
        else:
            rest.append(t)
            i += 1
    out["rest"] = " ".join(rest)
    return out


def _parse_ranges(spec, total):
    """Parse ``1-5,7,10-12`` into a sorted list of 0-based page indices.

    Empty spec -> all pages. Returns ``(indices, error_message)``.
    """
    if not spec:
        return list(range(total)), None
    indices = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                return [], f"Invalid range: `{part}`"
            if start < 1 or end < start or end > total:
                return [], f"Range `{part}` out of bounds (1-{total})"
            indices.update(range(start - 1, end))
        else:
            try:
                p = int(part)
            except ValueError:
                return [], f"Invalid page: `{part}`"
            if p < 1 or p > total:
                return [], f"Page `{part}` out of bounds (1-{total})"
            indices.add(p - 1)
    return sorted(indices), None


def _split_targets(spec, total):
    """Build split targets from a range spec.

    Each comma part becomes one output file. Empty spec -> one file per page.
    Returns ``(targets, error_message)`` where targets is a list of
    ``(label, [0-based indices])``.
    """
    if not spec:
        return [(str(i + 1), [i]) for i in range(total)], None
    targets = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                return [], f"Invalid range: `{part}`"
            if start < 1 or end < start or end > total:
                return [], f"Range `{part}` out of bounds (1-{total})"
            targets.append((f"{start}-{end}", list(range(start - 1, end))))
        else:
            try:
                p = int(part)
            except ValueError:
                return [], f"Invalid page: `{part}`"
            if p < 1 or p > total:
                return [], f"Page `{part}` out of bounds (1-{total})"
            targets.append((str(p), [p - 1]))
    return targets, None


async def _page_progress(msg, current, total, action, state):
    """Throttled progress edit for page-based operations."""
    if total <= 0:
        return
    now = time.time()
    pct = int(current * 100 / total)
    if current == total or now - state.get("last", 0) >= 3:
        state["last"] = now
        try:
            await msg.edit(f"`{action} {current}/{total} pages ({pct}%)...`")
        except Exception:
            pass


def _render_pages_to_images(path, page_indices, dpi=150):
    """Render 0-based page indices to PIL images.

    Returns a list of PIL images, or ``None`` when no renderer is available.
    """
    if not _CAN_RENDER:
        return None
    try:
        if _HAVE_FITZ and Image is not None:
            doc = fitz.open(path)
            imgs = []
            for idx in page_indices:
                pix = doc[idx].get_pixmap(dpi=dpi)
                imgs.append(
                    Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                )
            doc.close()
            return imgs
        # pdf2image
        first = min(page_indices) + 1
        last = max(page_indices) + 1
        images = convert_from_path(path, dpi=dpi, first_page=first, last_page=last)
        return [images[idx - (first - 1)] for idx in page_indices]
    except Exception as e:
        LOGS.info(f"{__file__}: page rendering failed: {e}")
        return None


async def _download_reply_pdf(event, xx, prefix="src"):
    """Download the replied PDF into the ``pdf/`` working dir.

    Returns ``(path, error_message_or_None)``.
    """
    ok = await event.get_reply_message()
    if not (ok and ok.document and ok.document.mime_type == "application/pdf"):
        return None, "`Reply to a PDF file.`"
    name = ok.file.name or "file.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    path = os.path.join("pdf", fns.check_filename(f"{prefix}_{name}"))
    try:
        result = await fns.downloader(
            path, ok.media.document, xx, time.time(), f"Downloading {name}..."
        )
    except Exception as e:
        return None, f"`Download failed: {e}`"
    return getattr(result, "name", path), None


async def _merge_files(paths, msg):
    """Merge a list of PDF paths into one file with progress.

    Returns ``(total_pages, merged_path, error_message)``.
    """
    if not _have_pdf_lib():
        return 0, None, "`No PDF library installed (need pypdf/PyPDF2).`"
    # First pass: validate every file and count pages.
    readers = []
    total = 0
    for n, p in enumerate(paths, 1):
        reader, npages, err = _safe_open(p)
        if err:
            for r, _ in readers:
                _close_reader(r)
            return 0, None, f"{err}\n(file {n}: `{os.path.basename(p)}`)"
        readers.append((reader, npages))
        total += npages
    # Second pass: copy pages with progress.
    writer = PdfWriter()
    merged = os.path.join("pdf", fns.check_filename("_merged.pdf"))
    state = {"last": 0}
    done = 0
    for reader, npages in readers:
        for i in range(npages):
            _add_page(writer, _get_page(reader, i))
            done += 1
            await _page_progress(msg, done, total, "Merging", state)
    try:
        with open(merged, "wb") as f:
            writer.write(f)
    except Exception as e:
        for r, _ in readers:
            _close_reader(r)
        _close_writer(writer)
        return 0, None, f"`Failed to write merged PDF: {e}`"
    for r, _ in readers:
        _close_reader(r)
    _close_writer(writer)
    return total, merged, None


async def _collect_reply_pdfs(event, reply):
    """Collect PDF messages from a reply (handles media-group albums)."""
    pdfs = []
    if reply.document and reply.document.mime_type == "application/pdf":
        pdfs.append(reply)
    if reply.grouped_id:
        try:
            nearby = await event.client.get_messages(
                event.chat_id, min_id=reply.id - 100, max_id=reply.id + 10
            )
            for m in nearby:
                if (
                    m.grouped_id == reply.grouped_id
                    and m.document
                    and m.document.mime_type == "application/pdf"
                ):
                    pdfs.append(m)
        except Exception:
            pass
    seen = set()
    uniq = []
    for m in pdfs:
        if m.id not in seen:
            seen.add(m.id)
            uniq.append(m)
    return uniq


async def _ocr_pdf(path, page_indices, msg, action):
    """OCR-extract text from the given 0-based page indices.

    Falls back to embedded text extraction when no OCR engine is available.
    Returns ``(text, used_real_ocr, error_message)``.
    """
    total = len(page_indices)
    state = {"last": 0}
    parts = []
    used_ocr = False
    if _CAN_OCR:
        used_ocr = True
        try:
            if _HAVE_FITZ and Image is not None:
                doc = fitz.open(path)
                for n, idx in enumerate(page_indices, 1):
                    pix = doc[idx].get_pixmap(dpi=200)
                    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    parts.append(f"Page {idx + 1}\n" + "-" * 100 + "\n")
                    parts.append(pytesseract.image_to_string(img) + "\n")
                    await _page_progress(msg, n, total, action, state)
                doc.close()
            else:  # pdf2image
                first = min(page_indices) + 1
                last = max(page_indices) + 1
                images = convert_from_path(
                    path, dpi=200, first_page=first, last_page=last
                )
                for n, idx in enumerate(page_indices, 1):
                    img = images[idx - (first - 1)]
                    parts.append(f"Page {idx + 1}\n" + "-" * 100 + "\n")
                    parts.append(pytesseract.image_to_string(img) + "\n")
                    await _page_progress(msg, n, total, action, state)
        except Exception as e:
            LOGS.info(f"{__file__}: OCR failed, falling back to embedded text: {e}")
            used_ocr = False
            parts = []
    if not used_ocr:
        reader, _n, err = _safe_open(path)
        if err:
            return "", False, err
        for m, idx in enumerate(page_indices, 1):
            parts.append(f"Page {idx + 1}\n" + "-" * 100 + "\n")
            parts.append(_extract_text(_get_page(reader, idx)) + "\n")
            await _page_progress(msg, m, total, f"{action} (embedded text)", state)
        _close_reader(reader)
    return "".join(parts), used_ocr, None


# --------------------------------------------------------------------------- #
#  Commands
# --------------------------------------------------------------------------- #
@ultroid_cmd(
    pattern="pdf( (.*)|$)",
)
async def pdfseimg(event):
    """Extract PDF page(s) as image(s)."""
    ok = await event.get_reply_message()
    msg = event.pattern_match.group(1).strip()
    if not (ok and ok.document and ok.document.mime_type == "application/pdf"):
        await event.eor("`Reply to the PDF you want to extract pages from.`")
        return
    if not _have_pdf_lib():
        await event.eor("`No PDF library installed (need pypdf/PyPDF2).`")
        return
    xx = await event.eor(get_string("com_1"))
    path, derr = await _download_reply_pdf(event, xx, "src")
    if derr:
        await xx.edit(derr)
        return
    reader, total, err = _safe_open(path)
    if err:
        await xx.edit(err)
        _cleanup(path)
        return
    # Resolve the requested page indices.
    if not msg:
        indices = list(range(total))
    elif "-" in msg and "," not in msg:
        u, d = msg.split("-", 1)
        try:
            s, e = int(u), int(d)
        except ValueError:
            await xx.edit(f"`Invalid range: {msg}`")
            _cleanup(path)
            return
        if s < 1 or e < s or e > total:
            await xx.edit(f"`Range out of bounds (1-{total}).`")
            _cleanup(path)
            return
        indices = list(range(s - 1, e))
    else:
        try:
            p = int(msg)
        except ValueError:
            await xx.edit(f"`Invalid page: {msg}`")
            _cleanup(path)
            return
        if p < 1 or p > total:
            await xx.edit(f"`Page out of bounds (1-{total}).`")
            _cleanup(path)
            return
        indices = [p - 1]

    if len(indices) > _MAX_IMG_PAGES:
        await xx.edit(
            f"`Too many pages ({len(indices)}/{total}). "
            f"Please specify a range (e.g. 1-{_MAX_IMG_PAGES}).`"
        )
        _cleanup(path)
        return

    state = {"last": 0}
    await xx.edit(f"`Extracting {len(indices)}/{total} page(s)...`")
    out_files = []
    rendered = _render_pages_to_images(path, indices)
    if rendered is not None:
        for n, img in enumerate(rendered, 1):
            fil = os.path.join("pdf", f"ult_{indices[n - 1] + 1}.png")
            img.save(fil, "PNG")
            out_files.append(fil)
            await _page_progress(xx, n, len(indices), "Rendering", state)
    else:
        # Fallback: emit one single-page PDF per requested page.
        for n, idx in enumerate(indices, 1):
            pw = PdfWriter()
            _add_page(pw, _get_page(reader, idx))
            fil = os.path.join("pdf", f"ult_{idx + 1}.pdf")
            with open(fil, "wb") as f:
                pw.write(f)
            _close_writer(pw)
            out_files.append(fil)
            await _page_progress(xx, n, len(indices), "Extracting", state)

    try:
        await event.client.send_file(
            event.chat_id, out_files, reply_to=event.reply_to_msg_id
        )
    except PhotoSaveFileInvalidError:
        await event.client.send_file(
            event.chat_id,
            out_files,
            reply_to=event.reply_to_msg_id,
            force_document=True,
        )
    except Exception as e:
        await xx.edit(f"`Failed to send pages: {e}`")
        for f in out_files:
            _cleanup(f)
        _cleanup(path)
        return
    await xx.delete()
    for f in out_files:
        _cleanup(f)
    _cleanup(path)


@ultroid_cmd(
    pattern="pdtext( (.*)|$)",
)
async def pdfsetxt(event):
    """Extract text from a PDF."""
    ok = await event.get_reply_message()
    msg = event.pattern_match.group(1).strip()
    if not (ok and ok.document and ok.document.mime_type == "application/pdf"):
        await event.eor("`Reply to the PDF you want to extract text from.`")
        return
    if not _have_pdf_lib():
        await event.eor("`No PDF library installed (need pypdf/PyPDF2).`")
        return
    xx = await event.eor(get_string("com_1"))
    path, derr = await _download_reply_pdf(event, xx, "txt")
    if derr:
        await xx.edit(derr)
        return
    reader, total, err = _safe_open(path)
    if err:
        await xx.edit(err)
        _cleanup(path)
        return
    # Resolve the requested page indices.
    if not msg:
        indices = list(range(total))
    elif "-" in msg and "," not in msg:
        u, d = msg.split("-", 1)
        try:
            s, e = int(u), int(d)
        except ValueError:
            await xx.edit(f"`Invalid range: {msg}`")
            _cleanup(path)
            return
        if s < 1 or e < s or e > total:
            await xx.edit(f"`Range out of bounds (1-{total}).`")
            _cleanup(path)
            return
        indices = list(range(s - 1, e))
    else:
        try:
            p = int(msg)
        except ValueError:
            await xx.edit(f"`Invalid page: {msg}`")
            _cleanup(path)
            return
        if p < 1 or p > total:
            await xx.edit(f"`Page out of bounds (1-{total}).`")
            _cleanup(path)
            return
        indices = [p - 1]

    state = {"last": 0}
    parts = []
    for n, idx in enumerate(indices, 1):
        parts.append(f"Page {idx + 1}\n" + "-" * 100 + "\n")
        parts.append(_extract_text(_get_page(reader, idx)) + "\n")
        await _page_progress(xx, n, len(indices), "Extracting text", state)
    text = "".join(parts)
    if not text.strip():
        await xx.edit(
            "`No text could be extracted (the PDF may be image-only — try .pdocr for OCR).`"
        )
        _cleanup(path)
        return
    txtfile = fns.check_filename(
        os.path.splitext(ok.file.name or "file.pdf")[0]
        + (f" {msg}" if msg else "")
        + ".txt"
    )
    with open(txtfile, "w", encoding="utf-8") as f:
        f.write(text)
    await event.client.send_file(event.chat_id, txtfile, reply_to=event.reply_to_msg_id)
    await xx.delete()
    _cleanup(txtfile)
    _cleanup(path)


@ultroid_cmd(
    pattern="pdocr( (.*)|$)",
)
async def pdocr_cmd(event):
    """OCR-extract text from a PDF (falls back to embedded text)."""
    flags = _parse_flags(event.pattern_match.group(1).strip())
    ok = await event.get_reply_message()
    if not (ok and ok.document and ok.document.mime_type == "application/pdf"):
        await event.eor("`Reply to a PDF file to OCR-extract its text.`")
        return
    if not _have_pdf_lib():
        await event.eor("`No PDF library installed (need pypdf/PyPDF2).`")
        return
    xx = await event.eor(get_string("com_1"))
    path, derr = await _download_reply_pdf(event, xx, "ocr")
    if derr:
        await xx.edit(derr)
        return
    reader, total, err = _safe_open(path)
    if err:
        await xx.edit(err)
        _cleanup(path)
        return
    indices, rerr = _parse_ranges(flags["ranges"], total)
    if rerr:
        await xx.edit(rerr)
        _cleanup(path)
        return
    if len(indices) > _MAX_OCR_PAGES:
        await xx.edit(
            f"`Too many pages to OCR ({len(indices)}/{total}). "
            f"Use -r to select a range (e.g. -r 1-{_MAX_OCR_PAGES}).`"
        )
        _cleanup(path)
        return
    _close_reader(reader)
    await xx.edit(f"`Running OCR on {len(indices)}/{total} page(s)...`")
    text, used_ocr, oerr = await _ocr_pdf(path, indices, xx, "OCR")
    if oerr:
        await xx.edit(oerr)
        _cleanup(path)
        return
    if not text.strip():
        await xx.edit("`OCR returned no text.`")
        _cleanup(path)
        return
    base = flags["output"] or os.path.splitext(ok.file.name or "file.pdf")[0]
    txtfile = fns.check_filename(_sanitize_name(base) + " (OCR).txt")
    with open(txtfile, "w", encoding="utf-8") as f:
        f.write(text)
    await event.client.send_file(event.chat_id, txtfile, reply_to=event.reply_to_msg_id)
    note = "OCR" if used_ocr else "embedded text (OCR engine unavailable)"
    await xx.edit(f"`Done — extracted via {note}, {len(indices)} page(s).`")
    _cleanup(txtfile)
    _cleanup(path)


@ultroid_cmd(
    pattern="pdsplit( (.*)|$)",
)
async def pdsplit_cmd(event):
    """Split a PDF into separate files (per page or per range)."""
    flags = _parse_flags(event.pattern_match.group(1).strip())
    ok = await event.get_reply_message()
    if not (ok and ok.document and ok.document.mime_type == "application/pdf"):
        await event.eor("`Reply to a PDF file to split it.`")
        return
    if not _have_pdf_lib():
        await event.eor("`No PDF library installed (need pypdf/PyPDF2).`")
        return
    xx = await event.eor(get_string("com_1"))
    path, derr = await _download_reply_pdf(event, xx, "split")
    if derr:
        await xx.edit(derr)
        return
    reader, total, err = _safe_open(path)
    if err:
        await xx.edit(err)
        _cleanup(path)
        return
    targets, rerr = _split_targets(flags["ranges"], total)
    if rerr:
        await xx.edit(rerr)
        _close_reader(reader)
        _cleanup(path)
        return
    if len(targets) > _MAX_SPLIT_PARTS:
        await xx.edit(
            f"`That would produce {len(targets)} files (max {_MAX_SPLIT_PARTS}). "
            f"Use -r to group pages into fewer parts (e.g. -r 1-10,11-20).`"
        )
        _close_reader(reader)
        _cleanup(path)
        return
    base = flags["output"] or os.path.splitext(ok.file.name or "file.pdf")[0]
    base = _sanitize_name(base)
    await xx.edit(f"`Splitting into {len(targets)} part(s) from {total} pages...`")
    out_files = []
    state = {"last": 0}
    for n, (label, idxs) in enumerate(targets, 1):
        writer = PdfWriter()
        for i in idxs:
            _add_page(writer, _get_page(reader, i))
        outp = os.path.join("pdf", fns.check_filename(f"{base}_{label}.pdf"))
        try:
            with open(outp, "wb") as f:
                writer.write(f)
        except Exception as e:
            await xx.edit(f"`Failed to write part {label}: {e}`")
            _close_writer(writer)
            _close_reader(reader)
            for f in out_files:
                _cleanup(f)
            _cleanup(path)
            return
        _close_writer(writer)
        out_files.append(outp)
        await _page_progress(xx, n, len(targets), "Splitting", state)
    _close_reader(reader)
    try:
        await event.client.send_file(
            event.chat_id, out_files, reply_to=event.reply_to_msg_id
        )
    except Exception as e:
        await xx.edit(f"`Failed to send split files: {e}`")
        for f in out_files:
            _cleanup(f)
        _cleanup(path)
        return
    await xx.edit(f"`Done — split into {len(out_files)} file(s) from {total} pages.`")
    for f in out_files:
        _cleanup(f)
    _cleanup(path)


@ultroid_cmd(
    pattern="pdmerge( (.*)|$)",
)
async def pdmerge_cmd(event):
    """Merge multiple PDFs (single or album) into one."""
    flags = _parse_flags(event.pattern_match.group(1).strip())
    reply = await event.get_reply_message()
    if not (reply and reply.media):
        await event.eor("`Reply to a PDF (or an album of PDFs) to merge them.`")
        return
    if not _have_pdf_lib():
        await event.eor("`No PDF library installed (need pypdf/PyPDF2).`")
        return
    xx = await event.eor(get_string("com_1"))
    pdfs = await _collect_reply_pdfs(event, reply)
    if not pdfs:
        await xx.edit("`No PDF files found in the reply.`")
        return
    dl_files = []
    for n, m in enumerate(pdfs, 1):
        fname = m.file.name or f"merge_{n}.pdf"
        if not fname.lower().endswith(".pdf"):
            fname += ".pdf"
        dest = os.path.join("pdf", fns.check_filename(f"m_{n}_{fname}"))
        try:
            res = await fns.downloader(
                dest, m.media.document, xx, time.time(), f"Downloading {fname}..."
            )
        except Exception as e:
            await xx.edit(f"`Download failed for {fname}: {e}`")
            for f in dl_files:
                _cleanup(f)
            return
        dl_files.append(getattr(res, "name", dest))

    total_pages, merged, merr = await _merge_files(dl_files, xx)
    if merr:
        await xx.edit(merr)
        for f in dl_files:
            _cleanup(f)
        _cleanup(merged)
        return
    out = _sanitize_name(flags["output"] or "Merged") + ".pdf"
    final = fns.check_filename(out)
    try:
        shutil.move(merged, final)
    except Exception as e:
        await xx.edit(f"`Failed to finalise merged PDF: {e}`")
        for f in dl_files:
            _cleanup(f)
        _cleanup(merged)
        return
    try:
        await event.client.send_file(
            event.chat_id, final, reply_to=event.reply_to_msg_id
        )
    except Exception as e:
        await xx.edit(f"`Failed to send merged PDF: {e}`")
        _cleanup(final)
        for f in dl_files:
            _cleanup(f)
        return
    await xx.edit(f"`Done — merged {len(dl_files)} PDF(s), {total_pages} pages.`")
    _cleanup(final)
    for f in dl_files:
        _cleanup(f)


@ultroid_cmd(
    pattern="pdscan( (.*)|$)",
)
async def imgscan(event):
    ok = await event.get_reply_message()
    if not (ok and (ok.media)):
        await event.eor("`Reply The pdf u Want to Download..`")
        return
    if not (
        ok.photo
        or (ok.file.name and ok.file.name.endswith(("png", "jpg", "jpeg", "webp")))
    ):
        await event.eor("`Reply to a Image only...`")
        return
    ultt = await ok.download_media()
    xx = await event.eor(get_string("com_1"))
    image = cv2.imread(ultt)
    original_image = image.copy()
    ratio = image.shape[0] / 500.0
    hi, wid = image.shape[:2]
    ra = 500 / float(hi)
    dmes = (int(wid * ra), 500)
    image = cv2.resize(image, dmes, interpolation=cv2.INTER_AREA)
    image_yuv = cv2.cvtColor(image, cv2.COLOR_BGR2YUV)
    image_y = np.zeros(image_yuv.shape[:2], np.uint8)
    image_y[:, :] = image_yuv[:, :, 0]
    image_blurred = cv2.GaussianBlur(image_y, (3, 3), 0)
    edges = cv2.Canny(image_blurred, 50, 200, apertureSize=3)
    contours, hierarchy = cv2.findContours(
        edges,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    polygons = []
    for cnt in contours:
        hull = cv2.convexHull(cnt)
        polygons.append(cv2.approxPolyDP(hull, 0.01 * cv2.arcLength(hull, True), False))
        sortedPoly = sorted(polygons, key=cv2.contourArea, reverse=True)
        cv2.drawContours(image, sortedPoly[0], -1, (0, 0, 255), 5)
        simplified_cnt = sortedPoly[0]
    if len(simplified_cnt) == 4:
        try:
            from skimage.filters import threshold_local
        except ImportError:
            LOGS.info(f"Scikit-Image is not Installed.")
            await xx.edit("`Installing Scikit-Image...\nThis may take some long...`")
            _, __ = await fns.bash("pip install scikit-image")
            LOGS.info(_)
            from skimage.filters import threshold_local
        cropped_image = fns.four_point_transform(
            original_image,
            simplified_cnt.reshape(4, 2) * ratio,
        )
        gray_image = cv2.cvtColor(cropped_image, cv2.COLOR_BGR2GRAY)
        T = threshold_local(gray_image, 11, offset=10, method="gaussian")
        ok = (gray_image > T).astype("uint8") * 255
    if len(simplified_cnt) != 4:
        ok = cv2.detailEnhance(original_image, sigma_s=10, sigma_r=0.15)
    cv2.imwrite("o.png", ok)
    image1 = Image.open("o.png")
    im1 = image1.convert("RGB")
    scann = f"Scanned {ultt.split('.')[0]}.pdf"
    im1.save(scann)
    await event.client.send_file(event.chat_id, scann, reply_to=event.reply_to_msg_id)
    await xx.delete()
    os.remove(ultt)
    os.remove("o.png")
    os.remove(scann)


@ultroid_cmd(
    pattern="pdsave( (.*)|$)",
)
async def savepdf(event):
    ok = await event.get_reply_message()
    if not (ok and (ok.media)):
        await eor(
            event,
            "`Reply to Images/pdf which u want to merge as a single pdf..`",
        )
        return
    ultt = await ok.download_media()
    if ultt.endswith(("png", "jpg", "jpeg", "webp")):
        xx = await event.eor(get_string("com_1"))
        image = cv2.imread(ultt)
        original_image = image.copy()
        ratio = image.shape[0] / 500.0
        h_, _v = image.shape[:2]
        m_ = 500 / float(h_)
        image = cv2.resize(image, (int(_v * m_), 500), interpolation=cv2.INTER_AREA)
        image_yuv = cv2.cvtColor(image, cv2.COLOR_BGR2YUV)
        image_y = np.zeros(image_yuv.shape[:2], np.uint8)
        image_y[:, :] = image_yuv[:, :, 0]
        image_blurred = cv2.GaussianBlur(image_y, (3, 3), 0)
        edges = cv2.Canny(image_blurred, 50, 200, apertureSize=3)
        contours, hierarchy = cv2.findContours(
            edges,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        polygons = []
        for cnt in contours:
            hull = cv2.convexHull(cnt)
            polygons.append(
                cv2.approxPolyDP(hull, 0.01 * cv2.arcLength(hull, True), False),
            )
            sortedPoly = sorted(polygons, key=cv2.contourArea, reverse=True)
            cv2.drawContours(image, sortedPoly[0], -1, (0, 0, 255), 5)
            simplified_cnt = sortedPoly[0]
        if len(simplified_cnt) == 4:
            try:
                from skimage.filters import threshold_local
            except ImportError:
                LOGS.info(f"Scikit-Image is not Installed.")
                await xx.edit(
                    "`Installing Scikit-Image...\nThis may take some long...`"
                )
                _, __ = await fns.bash("pip install scikit-image")
                LOGS.info(_)
                from skimage.filters import threshold_local
            cropped_image = fns.four_point_transform(
                original_image,
                simplified_cnt.reshape(4, 2) * ratio,
            )
            gray_image = cv2.cvtColor(cropped_image, cv2.COLOR_BGR2GRAY)
            T = threshold_local(gray_image, 11, offset=10, method="gaussian")
            ok = (gray_image > T).astype("uint8") * 255
        if len(simplified_cnt) != 4:
            ok = cv2.detailEnhance(original_image, sigma_s=10, sigma_r=0.15)
        cv2.imwrite("o.png", ok)
        image1 = Image.open("o.png")
        im1 = image1.convert("RGB")
        a = fns.check_filename("pdf/scan.pdf")
        im1.save(a)
        await xx.edit(
            f"Done, Now Reply Another Image/pdf if completed then use {HNDLR}pdsend to merge nd send all as pdf",
        )
        os.remove("o.png")
    elif ultt.endswith(".pdf"):
        a = fns.check_filename("pdf/scan.pdf")
        await event.client.download_media(ok, a)
        await eor(
            event,
            f"Done, Now Reply Another Image/pdf if completed then use {HNDLR}pdsend to merge nd send all as pdf",
        )
    else:
        await event.eor("`Reply to a Image/pdf only...`")
    os.remove(ultt)


@ultroid_cmd(
    pattern="pdsend( (.*)|$)",
)
async def sendpdf(event):
    """Merge & send the PDFs collected via .pdsave."""
    flags = _parse_flags(event.pattern_match.group(1).strip())
    afl = sorted(glob.glob("pdf/*.pdf"))
    if not afl:
        await eor(
            event,
            "first select pages by replying .pdsave of which u want to make multi page pdf file",
        )
        return
    if not _have_pdf_lib():
        await eor(event, "`No PDF library installed (need pypdf/PyPDF2).`")
        return
    xx = await event.eor(get_string("com_1"))
    total_pages, merged, merr = await _merge_files(afl, xx)
    if merr:
        await xx.edit(merr)
        return
    out = _sanitize_name(flags["output"] or "My PDF File") + ".pdf"
    final = fns.check_filename(out)
    try:
        shutil.move(merged, final)
    except Exception as e:
        await xx.edit(f"`Failed to finalise merged PDF: {e}`")
        _cleanup(merged)
        return
    try:
        await event.client.send_file(
            event.chat_id, final, reply_to=event.reply_to_msg_id
        )
    except Exception as e:
        await xx.edit(f"`Failed to send merged PDF: {e}`")
        _cleanup(final)
        return
    await xx.edit(f"`Done — merged {len(afl)} file(s), {total_pages} pages.`")
    _cleanup(final)
    shutil.rmtree("pdf", ignore_errors=True)
    os.makedirs("pdf", exist_ok=True)

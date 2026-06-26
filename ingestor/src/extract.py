"""Text extraction from PDF, DOCX, MD, TXT files."""
import pathlib


def extract_text(path: pathlib.Path) -> str:
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return _extract_pdf(path)
    elif suffix in (".docx", ".doc"):
        return _extract_docx(path)
    elif suffix in (".xlsx", ".xls"):
        return _extract_excel(path)
    elif suffix in (".md", ".txt", ".text"):
        return path.read_text(encoding="utf-8", errors="replace")
    elif suffix in (".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp"):
        return _extract_image(path)
    else:
        # Try reading as plain text for unknown types
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            raise ValueError(f"Unsupported file type: {suffix}")


def _extract_pdf(path: pathlib.Path) -> str:
    # Primary: pymupdf (handles more variants)
    try:
        import fitz  # type: ignore
        doc = fitz.open(str(path))
        parts = [page.get_text() for page in doc]
        doc.close()
        text = "\n\n".join(p.strip() for p in parts if p.strip())
        if text:
            return text
    except Exception:
        pass
    # Fallback: pypdf
    text = ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        parts = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(p.strip() for p in parts if p.strip())
    except Exception:
        pass
    if text:
        return text
    # Last resort: OCR via tesseract (image-only scanned PDFs)
    return _ocr_pdf(path)


def _ocr_pdf(path: pathlib.Path) -> str:
    try:
        import pytesseract
        from pdf2image import convert_from_path  # type: ignore
        images = convert_from_path(str(path), dpi=200)
        parts = [pytesseract.image_to_string(img) for img in images]
        return "\n\n".join(p for p in parts if p.strip())
    except Exception as e:
        print(f"[extract] OCR failed for {path.name}: {e}")
        return ""


def extract_bytes(data: bytes, filename: str) -> str:
    """Extract text from raw file bytes — used by email-sync for attachment OCR."""
    import tempfile, pathlib as _pl
    suffix = _pl.Path(filename).suffix.lower() or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = _pl.Path(tmp.name)
    try:
        return extract_text(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _extract_excel(path: pathlib.Path) -> str:
    suffix = path.suffix.lower()
    # Primary: openpyxl for .xlsx
    if suffix == ".xlsx":
        try:
            import openpyxl  # type: ignore
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            parts = []
            for ws in wb.worksheets:
                sheet_rows = []
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        sheet_rows.append("\t".join(cells))
                if sheet_rows:
                    parts.append(f"[Sheet: {ws.title}]\n" + "\n".join(sheet_rows))
            wb.close()
            text = "\n\n".join(parts)
            if text.strip():
                return text
        except Exception as e:
            print(f"[extract] openpyxl failed for {path.name}: {e}")
    # Fallback: xlrd for .xls (and older .xlsx)
    try:
        import xlrd  # type: ignore
        wb = xlrd.open_workbook(str(path))
        parts = []
        for ws in wb.sheets():
            sheet_rows = []
            for rx in range(ws.nrows):
                cells = [str(ws.cell_value(rx, cx)) for cx in range(ws.ncols)
                         if ws.cell_value(rx, cx) not in (None, "")]
                if cells:
                    sheet_rows.append("\t".join(cells))
            if sheet_rows:
                parts.append(f"[Sheet: {ws.name}]\n" + "\n".join(sheet_rows))
        return "\n\n".join(parts)
    except Exception as e:
        print(f"[extract] xlrd failed for {path.name}: {e}")
        return ""


def _extract_docx(path: pathlib.Path) -> str:
    from docx import Document
    doc = Document(str(path))
    return "\n\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())


def _extract_image(path: pathlib.Path) -> str:
    try:
        import pytesseract
        from PIL import Image
        img  = Image.open(str(path))
        text = pytesseract.image_to_string(img).strip()
        if text:
            return f"[Image: {path.name}]\n\n{text}"
    except Exception as e:
        print(f"[extract] OCR failed for {path.name}: {e}")
    return f"[Image: {path.name}] (no text extracted)"

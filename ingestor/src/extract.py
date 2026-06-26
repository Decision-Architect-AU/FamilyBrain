"""Text extraction from PDF, DOCX, MD, TXT files."""
import pathlib


def extract_text(path: pathlib.Path) -> str:
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return _extract_pdf(path)
    elif suffix in (".docx", ".doc"):
        return _extract_docx(path)
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

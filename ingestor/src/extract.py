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
    else:
        # Try reading as plain text for unknown types
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            raise ValueError(f"Unsupported file type: {suffix}")


def _extract_pdf(path: pathlib.Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(p.strip() for p in pages if p.strip())


def _extract_docx(path: pathlib.Path) -> str:
    from docx import Document
    doc = Document(str(path))
    return "\n\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())

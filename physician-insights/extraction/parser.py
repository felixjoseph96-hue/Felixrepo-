"""Extract plain text from uploaded interview files (txt/md/docx/pdf)."""
from pathlib import Path


class ParseError(Exception):
    pass


def parse_text_file(path: Path) -> str:
    """Return the plain-text transcript contained in `path`.

    Supports .txt/.md (read directly), .docx (python-docx) and .pdf (pypdf).
    """
    ext = path.suffix.lower()

    if ext in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")

    if ext == ".docx":
        try:
            import docx
        except ImportError as e:
            raise ParseError(
                "python-docx is required to parse .docx files. Install it with "
                "`pip install python-docx`."
            ) from e
        document = docx.Document(str(path))
        return "\n".join(p.text for p in document.paragraphs)

    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise ParseError(
                "pypdf is required to parse .pdf files. Install it with "
                "`pip install pypdf`."
            ) from e
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    raise ParseError(f"Unsupported text file extension: {ext}")

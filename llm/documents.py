from io import BytesIO
import logging
from pathlib import Path
from typing import NamedTuple

from docx import Document
from docx.table import Table
from pypdf import PdfReader

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt"}
TEXT_REQUIRED = "텍스트가 있는 PDF나 Word 파일을 올려 주세요"


class DocumentReadError(Exception):
    """A document could not be read from memory."""


class DocumentTexts(NamedTuple):
    contract: str | None
    payslip: str | None = None


def _word_text(data: bytes) -> str:
    document = Document(BytesIO(data))
    lines = []
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            lines.extend(" | ".join(cell.text for cell in row.cells) for row in block.rows)
        else:
            lines.append(block.text)
    return "\n".join(lines)


def _plain_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentReadError("TXT 파일의 인코딩을 UTF-8로 저장한 뒤 다시 올려 주세요.")


def read_document(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentReadError(TEXT_REQUIRED)
    try:
        if suffix == ".pdf":
            # Suppress parser diagnostics that can contain bytes from a malformed document.
            logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)
            reader = PdfReader(BytesIO(content))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        elif suffix == ".docx":
            text = _word_text(content)
        else:
            text = _plain_text(content)
    except DocumentReadError:
        raise
    except Exception:
        raise DocumentReadError("서류를 열 수 없어요. 파일이 손상됐거나 잠겨 있는지 확인해 주세요.") from None

    if "\x00" in text or sum(character.isalnum() for character in text) < 12:
        raise DocumentReadError(TEXT_REQUIRED)
    return text.strip()


# TODO: Add a separate image and scanned-PDF OCR path when image extraction is in scope.

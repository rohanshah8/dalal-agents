"""PDF documents (concall transcripts, annual reports) → plain text."""
from __future__ import annotations

import io
import logging
import re

from ..http import HttpClient

log = logging.getLogger(__name__)


class DocumentProvider:
    name = "bse-filings"

    def __init__(self, http: HttpClient):
        self.http = http

    def pdf_text(self, url: str, max_pages: int = 60) -> str:
        from pypdf import PdfReader

        raw = self.http.get_bytes(canonical_pdf_url(url), ttl_s=60 * 86400, timeout=60, retries=2)
        if not raw.startswith(b"%PDF"):
            raise ValueError(f"not a PDF: {url}")
        reader = PdfReader(io.BytesIO(raw))
        pages = []
        for i, page in enumerate(reader.pages):
            if i >= max_pages:
                break
            try:
                pages.append(page.extract_text() or "")
            except Exception as e:  # malformed page
                log.debug("pdf page %d failed: %s", i, e)
        return clean_text("\n".join(pages))


def clean_text(text: str) -> str:
    text = text.replace(" ", " ")
    text = re.sub(r"-\n(\w)", r"\1", text)  # de-hyphenate line breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_BSE_ANN = re.compile(r"https?://(?:www\.)?bseindia\.com/stockinfo/AnnPdfOpen\.aspx\?Pname=([\w\-]+\.pdf)", re.I)


def canonical_pdf_url(url: str) -> str:
    """BSE's AnnPdfOpen.aspx 302-redirects to a static path that is faster and more reliable."""
    m = _BSE_ANN.match(url)
    return f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{m.group(1)}" if m else url

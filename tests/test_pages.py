import asyncio

import aiohttp
import pytest

from evaluators import pages

URL = "https://example.org/a"
HTML = b"<html><head><title>A</title></head><body><p>Orin won.</p></body></html>"


class FakeContent:
    def __init__(self, body):
        self.body = body

    async def iter_chunked(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start : start + size]


class FakeResponse:
    def __init__(self, status=200, body=HTML, content_type="text/html"):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.content = FakeContent(body)
        self.content_length = len(body)
        self.charset = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.headers = []

    def get(self, url, **kwargs):
        self.headers.append(kwargs["headers"])
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def minimal_pdf(text):
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return out


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(pages, "RETRY_DELAY", 0)


def fetch(session, cache_dir, refresh=False):
    return asyncio.run(
        pages._fetch_page(
            session,
            URL,
            cache_dir=cache_dir,
            timeout=5,
            max_bytes=pages.DEFAULT_MAX_BYTES,
            max_pdf_bytes=pages.DEFAULT_MAX_PDF_BYTES,
            max_text_chars=pages.DEFAULT_MAX_TEXT_CHARS,
            max_redirects=5,
            refresh=refresh,
        )
    )


def test_bot_agent_first_and_browser_agent_after_a_refusal(tmp_path):
    session = FakeSession(FakeResponse())
    page = fetch(session, tmp_path)
    assert page["status"] == "ok" and page["text"] == "Orin won."
    assert page["user_agent"] == "bot"
    assert session.headers[0]["User-Agent"].startswith("DesearchSearchEvals/")
    assert "text/html" in session.headers[0]["Accept"]
    assert "application/pdf" in session.headers[0]["Accept"]

    session = FakeSession(FakeResponse(403), FakeResponse())
    page = fetch(session, tmp_path / "refused")
    assert page["status"] == "ok" and page["user_agent"] == "browser"
    assert [h["User-Agent"][:7] for h in session.headers] == ["Desearc", "Mozilla"]


@pytest.mark.parametrize(
    "first",
    [
        FakeResponse(503),
        FakeResponse(429),
        aiohttp.ServerTimeoutError("slow"),
        asyncio.TimeoutError(),
    ],
)
def test_transient_failures_are_retried_once(tmp_path, first):
    session = FakeSession(first, FakeResponse())
    page = fetch(session, tmp_path)
    assert (page["status"], page["attempts"], len(session.headers)) == ("ok", 2, 2)


def test_retry_gives_up_after_the_second_failure(tmp_path):
    session = FakeSession(FakeResponse(503), FakeResponse(503))
    page = fetch(session, tmp_path)
    assert (page["error"], page["attempts"]) == ("http_503", 2)


def test_permanent_failures_are_not_retried(tmp_path):
    session = FakeSession(FakeResponse(404))
    page = fetch(session, tmp_path)
    assert (page["error"], page["attempts"]) == ("http_404", 1)


def test_pdf_text_is_extracted(tmp_path):
    pytest.importorskip("pypdf")
    body = minimal_pdf("Orin won the 1998 final.")
    session = FakeSession(FakeResponse(body=body, content_type="application/pdf"))
    page = fetch(session, tmp_path)
    assert page["status"] == "ok" and "Orin won the 1998 final." in page["text"]
    assert len(session.headers) == 1


def test_pdf_served_as_octet_stream_is_sniffed(tmp_path):
    pytest.importorskip("pypdf")
    body = minimal_pdf("Orin won.")
    session = FakeSession(
        FakeResponse(body=body, content_type="application/octet-stream")
    )
    assert fetch(session, tmp_path)["status"] == "ok"


def test_pdf_stays_failed_without_a_pdf_library(tmp_path, monkeypatch):
    monkeypatch.setattr(pages, "pypdf", None)
    body = minimal_pdf("Orin won.")
    session = FakeSession(FakeResponse(body=body, content_type="application/pdf"))
    assert fetch(session, tmp_path)["error"] == "unsupported_content_type"


def test_broken_pdf_is_a_failed_page(tmp_path):
    pytest.importorskip("pypdf")
    body = b"%PDF-1.4\nnot really a pdf"
    session = FakeSession(FakeResponse(body=body, content_type="application/pdf"))
    page = fetch(session, tmp_path)
    assert page["status"] == "unavailable" and page["error"] in {
        "pdf_extraction_failed",
        "empty_pdf_text",
    }


def test_cache_key_names_the_fetcher_version(tmp_path, monkeypatch):
    first = pages.cache_path(tmp_path, URL)
    monkeypatch.setattr(pages, "FETCHER_VERSION", pages.FETCHER_VERSION + 1)
    assert pages.cache_path(tmp_path, URL) != first


def test_cached_page_is_reused_and_old_failures_are_not(tmp_path, monkeypatch):
    fetch(FakeSession(FakeResponse(403), FakeResponse(403)), tmp_path)
    assert fetch(FakeSession(), tmp_path)["error"] == "http_403"
    monkeypatch.setattr(pages, "FETCHER_VERSION", pages.FETCHER_VERSION + 1)
    assert fetch(FakeSession(FakeResponse()), tmp_path)["status"] == "ok"

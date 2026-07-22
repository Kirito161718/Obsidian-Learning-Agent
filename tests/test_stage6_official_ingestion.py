import gzip
import hashlib
import shutil
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.core.config import BASE_DIR, Settings
from app.official_sources.errors import OfficialSourceError
from app.official_sources.fetcher import FetchResult, OfficialSourceFetcher
from app.official_sources.normalizer import OfficialContentNormalizer, build_section_manifest
from app.official_sources.snapshot_store import SnapshotStore
from app.official_sources.url_security import URLSecurityValidator


def public_resolver(host: str, port: int) -> list[str]:
    del host, port
    return ["93.184.216.34"]


@pytest.mark.parametrize("scheme", ["file", "ftp", "data", "javascript", "gopher", "smb"])
def test_forbidden_url_schemes_are_rejected(scheme):
    validator = URLSecurityValidator(Settings(_env_file=None), resolver=public_resolver)
    with pytest.raises(OfficialSourceError) as caught:
        validator.validate_registered(f"{scheme}://example.com/value")
    assert caught.value.code == "official_url_invalid"


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "169.254.1.1",
        "0.0.0.0",
        "224.0.0.1",
        "100.64.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
    ],
)
def test_private_and_special_addresses_are_rejected(address):
    validator = URLSecurityValidator(Settings(_env_file=None), resolver=lambda host, port: [address])
    with pytest.raises(OfficialSourceError) as caught:
        validator.validate_registered("https://official.example/docs")
    assert caught.value.code == "official_private_address_blocked"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://EXAMPLE.com", "https://example.com/"),
        ("https://example.com:443/a?b=1#fragment", "https://example.com/a?b=1"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),
    ],
)
def test_registered_url_is_canonical_and_host_is_server_derived(url, expected):
    result = URLSecurityValidator(Settings(_env_file=None), resolver=public_resolver).validate_registered(url)
    assert result.canonical_url == expected and result.host == "example.com"


def test_url_credentials_and_unregistered_redirect_host_are_rejected():
    validator = URLSecurityValidator(Settings(_env_file=None), resolver=public_resolver)
    with pytest.raises(OfficialSourceError):
        validator.validate_registered("https://user:secret@example.com/docs")
    with pytest.raises(OfficialSourceError) as caught:
        validator.validate_redirect("https://example.com/docs", "https://other.example/docs", allowed_host="example.com")
    assert caught.value.code == "official_redirect_blocked"


def test_https_downgrade_redirect_is_rejected():
    validator = URLSecurityValidator(Settings(_env_file=None), resolver=public_resolver)
    with pytest.raises(OfficialSourceError) as caught:
        validator.validate_redirect("https://example.com/docs", "http://example.com/docs", allowed_host="example.com")
    assert caught.value.code == "official_redirect_blocked"


class OfficialHandler(BaseHTTPRequestHandler):
    version = 1

    def do_GET(self):
        if self.path == "/redirect-other":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_port}/document")
            self.end_headers()
            return
        if self.path == "/redirect-loop":
            self.send_response(302)
            self.send_header("Location", "/redirect-loop")
            self.end_headers()
            return
        if self.path == "/bad-type":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(b"bad")
            return
        if self.path == "/large":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "2000")
            self.end_headers()
            self.wfile.write(b"x" * 2000)
            return
        etag = f'"v{self.version}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        body = f"# Official\n\nVersion {self.version}\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", "Tue, 21 Jul 2026 00:00:00 GMT")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        del format, args


@contextmanager
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), OfficialHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_local_fetch_streams_content_and_supports_etag_304():
    with local_server() as server:
        settings = Settings(_env_file=None, app_env="test")
        fetcher = OfficialSourceFetcher(settings)
        url = f"http://127.0.0.1:{server.server_port}/document"
        first = fetcher.fetch(url, allowed_host="127.0.0.1")
        second = fetcher.fetch(url, allowed_host="127.0.0.1", etag=first.etag)
    assert first.body == b"# Official\n\nVersion 1\n"
    assert first.content_type == "text/markdown" and first.redirect_count == 0
    assert second.not_modified and second.body is None


@pytest.mark.parametrize(
    ("path", "setting", "code"),
    [
        ("/bad-type", {}, "official_content_type_unsupported"),
        ("/large", {"official_fetch_max_bytes": 1024}, "official_response_too_large"),
        ("/redirect-other", {}, "official_redirect_blocked"),
        ("/redirect-loop", {"official_fetch_max_redirects": 1}, "official_redirect_blocked"),
    ],
)
def test_fetch_limits_and_redirect_policy(path, setting, code):
    with local_server() as server:
        settings = Settings(_env_file=None, app_env="test", **setting)
        url = f"http://127.0.0.1:{server.server_port}{path}"
        with pytest.raises(OfficialSourceError) as caught:
            OfficialSourceFetcher(settings).fetch(url, allowed_host="127.0.0.1")
    assert caught.value.code == code


def test_html_normalization_removes_active_and_navigation_content_and_keeps_structure(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, app_env="test")
    normalizer = OfficialContentNormalizer(settings)
    body = b"""<html><body><nav>menu</nav><main><h1>Title</h1><p>Fact</p><script>steal()</script><table><tr><th>A</th></tr><tr><td>B</td></tr></table><pre>code()</pre></main></body></html>"""
    result = normalizer.normalize(body, content_type="text/html")
    assert "# Title" in result.text and "Fact" in result.text
    assert "menu" not in result.text and "steal" not in result.text
    assert "| A |" in result.text and "```" in result.text
    assert result.content_hash == hashlib.sha256(result.text.encode()).hexdigest()


def test_html_selector_and_no_match_are_deterministic():
    normalizer = OfficialContentNormalizer(Settings(_env_file=None, app_env="test"))
    result = normalizer.normalize(b"<main><p>A</p><section id='x'><h2>B</h2></section></main>", content_type="text/html", content_selector="#x")
    assert "## B" in result.text and "A" not in result.text
    with pytest.raises(OfficialSourceError) as caught:
        normalizer.normalize(b"<main>A</main>", content_type="text/html", content_selector="#missing")
    assert caught.value.code == "official_content_empty"


@pytest.mark.parametrize("content_type,source_type", [("text/markdown", "markdown"), ("text/plain", "text")])
def test_markdown_and_text_normalization_is_stable(content_type, source_type):
    normalizer = OfficialContentNormalizer(Settings(_env_file=None, app_env="test"))
    one = normalizer.normalize(b"\xef\xbb\xbf# T\r\n\r\nvalue  \r\n", content_type=content_type, source_type=source_type)
    two = normalizer.normalize(one.text.encode(), content_type=content_type, source_type=source_type)
    assert one.text == two.text == "# T\n\nvalue\n" and one.content_hash == two.content_hash


def test_invalid_utf8_and_source_type_mismatch_are_rejected():
    normalizer = OfficialContentNormalizer(Settings(_env_file=None, app_env="test"))
    with pytest.raises(OfficialSourceError):
        normalizer.normalize(b"\xff", content_type="text/plain")
    with pytest.raises(OfficialSourceError) as caught:
        normalizer.normalize(b"text", content_type="text/plain", source_type="html")
    assert caught.value.code == "official_content_type_unsupported"


def _minimal_pdf(text: str | None) -> bytes:
    stream = f"BT /F1 12 Tf 72 100 Td ({text or ''}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(data)); data.extend(f"{index} 0 obj\n".encode()+obj+b"\nendobj\n")
    xref = len(data); data.extend(f"xref\n0 {len(objects)+1}\n".encode()); data.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]: data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


def test_pdf_text_is_extracted_without_leaving_temporary_file(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "official_temp_dir", property(lambda self: tmp_path / "temp"))
    normalizer = OfficialContentNormalizer(Settings(_env_file=None, app_env="test"))
    result = normalizer.normalize(_minimal_pdf("Official Fact"), content_type="application/pdf", source_type="pdf")
    assert "Official Fact" in result.text and list((tmp_path / "temp").glob("*")) == []


def test_image_only_or_empty_pdf_is_not_fabricated(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "official_temp_dir", property(lambda self: tmp_path / "temp"))
    normalizer = OfficialContentNormalizer(Settings(_env_file=None, app_env="test"))
    with pytest.raises(OfficialSourceError) as caught:
        normalizer.normalize(_minimal_pdf(None), content_type="application/pdf", source_type="pdf")
    assert caught.value.code == "official_pdf_unreadable"


def test_section_ids_are_stable_and_manifest_has_no_full_body():
    text = "# A\nFact\n## B\nMore\n"
    one = build_section_manifest(text)
    two = build_section_manifest(text)
    assert one == two and one[0]["section_id"].startswith("section-")
    assert all("text" not in item and item["chars"] > 0 for item in one)


def test_snapshot_store_is_gzip_project_relative_and_rejects_path_escape(tmp_path, monkeypatch):
    root = BASE_DIR / "runtime" / "pytest-stage6" / tmp_path.name / "snapshots"
    monkeypatch.setattr(Settings, "official_snapshot_dir", property(lambda self: root))
    settings = Settings(_env_file=None, app_env="test")
    store = SnapshotStore(settings)
    try:
        relative = store.save("source", "snapshot", "# Fact\n")
        assert not Path(relative).is_absolute()
        path = BASE_DIR / relative
        assert gzip.open(path, "rt", encoding="utf-8").read() == "# Fact\n"
        assert store.load(relative) == "# Fact\n"
        with pytest.raises(OfficialSourceError):
            store.resolve("../secret.gz")
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)

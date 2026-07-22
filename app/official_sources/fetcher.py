from __future__ import annotations

import http.client
import socket
import ssl
from dataclasses import dataclass

from app.core.config import Settings
from app.official_sources.errors import OfficialSourceError, official_error
from app.official_sources.url_security import URLSecurityValidator, ValidatedURL

REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, validated: ValidatedURL, timeout: float) -> None:
        super().__init__(validated.host, validated.port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = validated.addresses[0]

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


@dataclass(frozen=True, slots=True)
class FetchResult:
    status_code: int
    final_url: str
    content_type: str | None
    body: bytes | None
    etag: str | None
    last_modified: str | None
    redirect_count: int

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304


class OfficialSourceFetcher:
    def __init__(self, settings: Settings, validator: URLSecurityValidator | None = None) -> None:
        self.settings = settings
        self.validator = validator or URLSecurityValidator(settings)

    def fetch(
        self,
        url: str,
        *,
        allowed_host: str,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        current = self.validator.validate_request(url, allowed_host=allowed_host)
        redirects = 0
        while True:
            result, location = self._request_once(current, etag=etag, last_modified=last_modified)
            if result.status_code not in REDIRECT_STATUSES:
                return FetchResult(
                    result.status_code,
                    current.canonical_url,
                    result.content_type,
                    result.body,
                    result.etag,
                    result.last_modified,
                    redirects,
                )
            if redirects >= self.settings.official_fetch_max_redirects:
                raise official_error("official_redirect_blocked", "Official source redirect limit exceeded")
            current = self.validator.validate_redirect(current.canonical_url, location or "", allowed_host=allowed_host)
            redirects += 1

    def _request_once(
        self,
        validated: ValidatedURL,
        *,
        etag: str | None,
        last_modified: str | None,
    ) -> tuple[FetchResult, str | None]:
        headers = {
            "Host": validated.host_header,
            "User-Agent": self.settings.official_fetch_user_agent,
            "Accept": ", ".join(self.settings.official_fetch_allowed_content_type_list),
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        if etag:
            headers["If-None-Match"] = etag[:500]
        if last_modified:
            headers["If-Modified-Since"] = last_modified[:500]
        connection: http.client.HTTPConnection
        if validated.scheme == "https":
            connection = _PinnedHTTPSConnection(validated, self.settings.official_fetch_timeout_seconds)
        else:
            connection = http.client.HTTPConnection(
                validated.addresses[0], validated.port, timeout=self.settings.official_fetch_timeout_seconds
            )
        try:
            connection.request("GET", validated.request_target, headers=headers)
            response = connection.getresponse()
            status = int(response.status)
            content_type_header = response.getheader("Content-Type")
            content_type = content_type_header.split(";", 1)[0].strip().casefold() if content_type_header else None
            location = response.getheader("Location")
            common = dict(
                status_code=status,
                final_url=validated.canonical_url,
                content_type=content_type,
                etag=self._safe_header(response.getheader("ETag")),
                last_modified=self._safe_header(response.getheader("Last-Modified")),
                redirect_count=0,
            )
            if status in REDIRECT_STATUSES:
                response.read(1024)
                return FetchResult(body=None, **common), location
            if status == 304:
                response.read(1024)
                return FetchResult(body=None, **common), None
            if status < 200 or status >= 300:
                response.read(1024)
                raise official_error("official_fetch_failed", f"Official source returned HTTP {status}", status_code=502)
            if content_type not in self.settings.official_fetch_allowed_content_type_list:
                raise official_error("official_content_type_unsupported", "Official source content type is not allowed")
            content_encoding = (response.getheader("Content-Encoding") or "identity").strip().casefold()
            if content_encoding not in {"", "identity"}:
                raise official_error("official_response_too_large", "Compressed official responses are not accepted")
            content_length = response.getheader("Content-Length")
            if content_length:
                try:
                    if int(content_length) > self.settings.official_fetch_max_bytes:
                        raise official_error("official_response_too_large", "Official source response is too large")
                except ValueError as exc:
                    raise official_error("official_fetch_failed", "Official source content length is invalid") from exc
            body = bytearray()
            while chunk := response.read(65_536):
                body.extend(chunk)
                if len(body) > self.settings.official_fetch_max_bytes:
                    raise official_error("official_response_too_large", "Official source response is too large")
            return FetchResult(body=bytes(body), **common), None
        except OfficialSourceError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise official_error("official_fetch_timeout", "Official source fetch timed out", status_code=504) from exc
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise official_error("official_fetch_failed", "Official source fetch failed", status_code=502) from exc
        finally:
            connection.close()

    @staticmethod
    def _safe_header(value: str | None) -> str | None:
        if not value:
            return None
        return " ".join(value.split())[:500]

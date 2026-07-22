from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

from app.core.config import Settings
from app.official_sources.errors import official_error

Resolver = Callable[[str, int], list[str]]
FORBIDDEN_SCHEMES = {"file", "ftp", "data", "javascript", "gopher", "smb"}
METADATA_ADDRESSES = {ipaddress.ip_address("169.254.169.254")}


@dataclass(frozen=True, slots=True)
class ValidatedURL:
    canonical_url: str
    scheme: str
    host: str
    port: int
    request_target: str
    addresses: tuple[str, ...]

    @property
    def host_header(self) -> str:
        default = 443 if self.scheme == "https" else 80
        display_host = f"[{self.host}]" if ":" in self.host else self.host
        return display_host if self.port == default else f"{display_host}:{self.port}"


class URLSecurityValidator:
    def __init__(self, settings: Settings, resolver: Resolver | None = None) -> None:
        self.settings = settings
        self.resolver = resolver or self._resolve

    def validate_registered(self, raw_url: str) -> ValidatedURL:
        return self.validate_request(raw_url, allowed_host=None)

    def validate_request(self, raw_url: str, *, allowed_host: str | None) -> ValidatedURL:
        if not raw_url or len(raw_url) > 2048 or any(character.isspace() for character in raw_url):
            raise official_error("official_url_invalid", "Official source URL is invalid")
        try:
            parsed = urlsplit(raw_url.strip())
        except ValueError as exc:
            raise official_error("official_url_invalid", "Official source URL is invalid") from exc
        scheme = parsed.scheme.casefold()
        if scheme in FORBIDDEN_SCHEMES or scheme not in {"https", "http"}:
            raise official_error("official_url_invalid", "Only HTTPS official sources are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise official_error("official_url_invalid", "Credentials are not allowed in official source URLs")
        try:
            raw_host = parsed.hostname
            port = parsed.port or (443 if scheme == "https" else 80)
        except ValueError as exc:
            raise official_error("official_url_invalid", "Official source URL port is invalid") from exc
        if not raw_host or port < 1 or port > 65535:
            raise official_error("official_url_invalid", "Official source URL host is invalid")
        try:
            host = raw_host.rstrip(".").encode("idna").decode("ascii").casefold()
        except UnicodeError as exc:
            raise official_error("official_url_invalid", "Official source URL host is invalid") from exc
        local_http = self._local_http_allowed(scheme, host)
        if scheme != "https" and not local_http:
            raise official_error("official_url_invalid", "Only HTTPS official sources are allowed")
        if allowed_host is not None and host != allowed_host.casefold().rstrip("."):
            raise official_error("official_host_not_allowed", "Redirect host is not registered")

        try:
            addresses = tuple(dict.fromkeys(self.resolver(host, port)))
        except (OSError, socket.gaierror) as exc:
            raise official_error("official_fetch_failed", "Official source host could not be resolved", status_code=502) from exc
        if not addresses:
            raise official_error("official_fetch_failed", "Official source host could not be resolved", status_code=502)
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address.split("%", 1)[0])
            except ValueError as exc:
                raise official_error("official_private_address_blocked", "Resolved address is invalid") from exc
            if self._address_forbidden(ip, local_http=local_http):
                raise official_error("official_private_address_blocked", "Private or special network addresses are blocked")

        path = parsed.path or "/"
        request_target = path + (f"?{parsed.query}" if parsed.query else "")
        display_host = f"[{host}]" if ":" in host else host
        netloc = display_host if port == (443 if scheme == "https" else 80) else f"{display_host}:{port}"
        canonical = urlunsplit(SplitResult(scheme, netloc, path, parsed.query, ""))
        return ValidatedURL(canonical, scheme, host, port, request_target, addresses)

    def validate_redirect(self, current_url: str, location: str, *, allowed_host: str) -> ValidatedURL:
        if not location or len(location) > 2048:
            raise official_error("official_redirect_blocked", "Redirect location is invalid")
        target = urljoin(current_url, location)
        current_scheme = urlsplit(current_url).scheme.casefold()
        target_scheme = urlsplit(target).scheme.casefold()
        if current_scheme == "https" and target_scheme != "https":
            raise official_error("official_redirect_blocked", "HTTPS downgrade redirects are blocked")
        try:
            return self.validate_request(target, allowed_host=allowed_host)
        except Exception as exc:
            if getattr(exc, "code", None) in {"official_host_not_allowed", "official_url_invalid"}:
                raise official_error("official_redirect_blocked", "Redirect target is not allowed") from exc
            raise

    def _local_http_allowed(self, scheme: str, host: str) -> bool:
        enabled = self.settings.app_env == "test" or self.settings.official_fetch_allow_local_http
        return enabled and scheme == "http" and host in {"localhost", "127.0.0.1"}

    @staticmethod
    def _address_forbidden(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, local_http: bool) -> bool:
        if ip in METADATA_ADDRESSES:
            return True
        if local_http and ip.is_loopback:
            return False
        if getattr(ip, "ipv4_mapped", None) is not None:
            ip = ip.ipv4_mapped  # type: ignore[assignment]
        return bool(
            not ip.is_global
            or ip.is_multicast
            or ip.is_unspecified
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_reserved
        )

    @staticmethod
    def _resolve(host: str, port: int) -> list[str]:
        return [item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]

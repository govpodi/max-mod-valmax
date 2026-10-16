"""Explicit Wikimedia Commons media search, independent from MAX bot/API tokens.

Only search(payload) performs a search. No draft text is polled or persisted.
HTTP inputs accept a short query or an opaque result ID, never a URL or path.
Returned files are staged for the native manual-send draft, never sent here.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, unquote, parse_qsl
from urllib.request import HTTPRedirectHandler, Request, build_opener

API = "https://commons.wikimedia.org/w/api.php"
PROVIDER = "Wikimedia Commons"
MEDIA_USER_AGENT = "ValMAX-MediaSearch/1.1 (https://github.com/govpodi/max-mod-valmax)"
MAX_RETRY_SECONDS = 86400
MAX_API_BYTES = 1_000_000
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
MAX_CACHE_BYTES = 128 * 1024 * 1024
RESULT_TTL_SECONDS = 20 * 60
STAGING_TTL_SECONDS = 24 * 60 * 60
RESULT_LIMIT = 6
ID_RE = re.compile(r"^[0-9a-f]{32}$")
CODES = frozenset({"invalid_input", "invalid_response", "invalid_remote_url", "file_too_large",
                   "unsupported_media", "provider_busy", "provider_unavailable", "storage_unavailable",
                   "result_expired", "search_too_fast", "preview_unavailable", "download_busy", "cache_full"})
HTTP_STATUSES = frozenset({400, 404, 409, 410, 413, 422, 429, 502, 503, 507})
MIMES = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
         "image/webp": ".webp", "video/webm": ".webm", "video/mp4": ".mp4"}


class InlineMediaError(Exception):
    """Finite error code only: no request, query, remote response or file path."""
    def __init__(self, code: str, status: int = 400, *, retry_after_seconds: int | None = None):
        if not isinstance(code, str) or code not in CODES or type(status) is not int or status not in HTTP_STATUSES:
            raise ValueError("Invalid inline media error classification")
        if retry_after_seconds is not None and (type(retry_after_seconds) is not int
                or not 1 <= retry_after_seconds <= MAX_RETRY_SECONDS):
            raise ValueError("Invalid inline media retry interval")
        super().__init__(code)
        self.code = code
        self.status = status
        self.retry_after_seconds = retry_after_seconds


def _retry_delay(header: object, now_wall: float) -> float:
    """Keep the provider's full deadline; malformed values wait at least 5s."""
    if not isinstance(header, str) or len(header) > 128:
        return 5.
    header = header.strip()
    if re.fullmatch(r"[0-9]{1,12}", header):
        return max(5., float(header))
    try:
        value = parsedate_to_datetime(header)
        if value.tzinfo is None:
            from datetime import timezone
            value = value.replace(tzinfo=timezone.utc)
        delay = value.timestamp() - now_wall
        return max(5., delay) if math.isfinite(delay) else 5.
    except (TypeError, ValueError, OverflowError):
        return 5.


class _ProviderGate:
    """One bounded queue and cooldown for Wikimedia API, previews and originals."""
    def __init__(self, *, clock=time.monotonic, wall_clock=time.time, max_parallel=2, queue_seconds=5):
        self.clock, self.wall_clock = clock, wall_clock
        self.max_parallel, self.queue_seconds = max_parallel, queue_seconds
        self._condition = threading.Condition()
        self._active = 0
        self._foreground_waiting = 0
        self._retry_until = 0.

    def busy_error(self):
        with self._condition:
            remaining = max(1, math.ceil(self._retry_until - self.clock()))
        # Do not advertise an earlier retry for a provider deadline over a day.
        delay = remaining if remaining <= MAX_RETRY_SECONDS else None
        return InlineMediaError("provider_busy", 503, retry_after_seconds=delay)

    def defer(self, header):
        delay = _retry_delay(header, self.wall_clock())
        with self._condition:
            self._retry_until = max(self._retry_until, self.clock() + delay)
            self._condition.notify_all()

    @contextmanager
    def request(self, *, preview=False):
        deadline = time.monotonic() + self.queue_seconds
        with self._condition:
            if not preview:
                self._foreground_waiting += 1
            try:
                while True:
                    if self.clock() < self._retry_until:
                        raise self.busy_error()
                    if self._active < self.max_parallel and (not preview or not self._foreground_waiting):
                        self._active += 1
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise InlineMediaError("provider_unavailable", 503)
                    self._condition.wait(remaining)
            finally:
                if not preview:
                    self._foreground_waiting -= 1
                    self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()


_PROVIDER_GATE = _ProviderGate()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
    def handle_data(self, data):
        self.parts.append(data)


def plain(value: object, limit: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    parser = _Text()
    try:
        parser.feed(value[:8000])
        text = " ".join("".join(parser.parts).split())
    except Exception:
        return ""
    return "".join(c for c in text if c >= " " and c not in "\x7f\u202a\u202b\u202d\u202e\u2066\u2067\u2068\u2069")[:limit]


def safe_url(value: object, kind: str) -> str:
    if not isinstance(value, str) or len(value) > 2048 or any(c <= " " for c in value) or "\\" in value:
        raise InlineMediaError("invalid_remote_url", 502)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise InlineMediaError("invalid_remote_url", 502) from None
    hosts = {"commons.wikimedia.org"} if kind in ("api", "source") else {"upload.wikimedia.org", "thumb.wikimedia.org"}
    decoded = unquote(parsed.path)
    if (parsed.scheme != "https" or parsed.hostname not in hosts or port not in (None, 443)
            or parsed.username is not None or parsed.password is not None or parsed.fragment
            or any(part in (".", "..") for part in decoded.split("/")) or "\\" in decoded):
        raise InlineMediaError("invalid_remote_url", 502)
    if kind == "api":
        valid = parsed.path == "/w/api.php"
    elif kind == "source":
        valid = parsed.path.startswith("/wiki/File:") and not parsed.query
    else:
        valid = parsed.path.startswith("/wikipedia/commons/") and not parsed.query
    if not valid:
        raise InlineMediaError("invalid_remote_url", 502)
    return value


def canonical_media_url(value: object) -> str:
    """Current Commons URLs add UTM attribution; strip only these known trackers."""
    if not isinstance(value, str):
        raise InlineMediaError("invalid_remote_url", 502)
    parsed = urlsplit(value)
    if parsed.query:
        fields = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=8)
        if not fields or any(key not in {"utm_source", "utm_campaign", "utm_content"} for key, _ in fields):
            raise InlineMediaError("invalid_remote_url", 502)
        value = parsed._replace(query="").geturl()
    return safe_url(value, "media")


class _Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        kind = "api" if urlsplit(req.full_url).path == "/w/api.php" else "media"
        newurl = safe_url(newurl, kind) if kind == "api" else canonical_media_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, limit: int, kind: str) -> tuple[bytes, str]:
    safe_url(url, kind)
    request = Request(url, headers={"User-Agent": MEDIA_USER_AGENT,
                                    "Accept": "application/json" if kind == "api" else "image/*,video/webm,video/mp4",
                                    "Accept-Encoding": "identity"})
    with _PROVIDER_GATE.request(preview=kind == "media" and limit <= MAX_PREVIEW_BYTES):
        try:
            with build_opener(_Redirects()).open(request, timeout=20) as response:
                safe_url(response.geturl(), kind)
                length = response.headers.get("Content-Length")
                if length and (not length.isdecimal() or int(length) > limit):
                    raise InlineMediaError("file_too_large", 413)
                if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                    raise InlineMediaError("unsupported_media", 422)
                data = response.read(limit + 1)
                mime = response.headers.get_content_type().lower()
        except InlineMediaError:
            raise
        except HTTPError as error:
            if error.code in (429, 503):
                _PROVIDER_GATE.defer(error.headers.get("Retry-After"))
                error.close()
                raise _PROVIDER_GATE.busy_error() from None
            error.close()
            raise InlineMediaError("provider_unavailable", 503) from None
        except (URLError, OSError, TimeoutError, ValueError):
            raise InlineMediaError("provider_unavailable", 503) from None
    if len(data) > limit:
        raise InlineMediaError("file_too_large", 413)
    return data, mime


def valid_content(data: bytes, mime: str) -> bool:
    if mime == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if mime == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if mime == "image/webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if mime == "video/webm":
        return data.startswith(b"\x1a\x45\xdf\xa3") and b"webm" in data[:4096]
    if mime == "video/mp4":
        return len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in (b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V ")
    return False


@dataclass(frozen=True)
class _Entry:
    row: dict
    url: str
    preview_url: str
    expires: float


class InlineMediaService:
    """One helper-owned local runtime cache, bounded public results and staging."""
    def __init__(self, cache_root: Path, *, transport=fetch, clock=time.monotonic):
        requested = Path(cache_root)
        if (not requested.is_absolute() or any(part in (".", "..") for part in requested.parts)
                or (os.name == "nt" and (not re.fullmatch(r"[A-Za-z]:", requested.drive)
                                         or any(":" in part for part in requested.parts[1:])))):
            raise ValueError("Unsafe media staging root")
        from launch_mod import _hold_directory
        # Hold every lexical ancestor before creating the next directory.
        # resolve() alone would accept an existing junction outside the cache.
        with ExitStack() as held:
            for directory in [*reversed(requested.parents), requested]:
                if not directory.exists():
                    directory.mkdir()
                held.enter_context(_hold_directory(directory))
            self.root = requested
        self.transport = transport
        self.clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._previews: OrderedDict[str, tuple[bytes, str, float]] = OrderedDict()
        self._preview_errors: OrderedDict[str, tuple[float, str, int, int | None]] = OrderedDict()
        self._preview_pending: dict[str, Future] = {}
        self._lock = threading.RLock()
        self._download_lock = threading.Lock()
        self._last_search = -float("inf")

    @contextmanager
    def _hold_root(self):
        from launch_mod import _hold_directory
        try:
            with ExitStack() as held:
                for directory in [*reversed(self.root.parents), self.root]:
                    held.enter_context(_hold_directory(directory))
                yield
        except (OSError, ValueError):
            raise InlineMediaError("storage_unavailable", 503) from None

    def _entry(self, result_id: object) -> _Entry:
        if not isinstance(result_id, str) or not ID_RE.fullmatch(result_id):
            raise InlineMediaError("invalid_input")
        with self._lock:
            entry = self._entries.get(result_id)
            if entry is None or entry.expires < self.clock():
                self._entries.pop(result_id, None)
                raise InlineMediaError("result_expired", 410)
            return entry

    def search(self, payload: object) -> dict:
        if not isinstance(payload, dict) or set(payload) != {"mode", "query"}:
            raise InlineMediaError("invalid_input")
        mode, query = payload["mode"], payload["query"]
        if mode not in ("pic", "vid") or not isinstance(query, str):
            raise InlineMediaError("invalid_input")
        query = query.strip()
        if not 2 <= len(query) <= 160 or any(ord(c) < 32 or c == "\x7f" for c in query):
            raise InlineMediaError("invalid_input")
        with self._lock:
            now = self.clock()
            if now - self._last_search < 2:
                raise InlineMediaError("search_too_fast", 429)
            self._last_search = now
        # Literal quoted query prevents user input from overriding the media filter.
        literal = query.replace('\\', ' ').replace('"', ' ')
        parameters = {"action": "query", "format": "json", "formatversion": "2", "generator": "search",
                      "gsrnamespace": "6", "gsrlimit": str(RESULT_LIMIT),
                      "gsrsearch": 'filetype:' + ("bitmap" if mode == "pic" else "video") + ' "' + literal + '"',
                      "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "240",
                      "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|AttributionRequired", "iiextmetadatalanguage": "ru"}
        raw, mime = self.transport(API + "?" + urlencode(parameters), MAX_API_BYTES, "api")
        if len(raw) > MAX_API_BYTES or mime not in ("application/json", "application/json; charset=utf-8"):
            raise InlineMediaError("provider_unavailable", 503)
        try:
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict) or "error" in body:
                raise ValueError()
            pages = body.get("query", {}).get("pages", [])
            if not isinstance(pages, list) or len(pages) > RESULT_LIMIT:
                raise ValueError()
        except (ValueError, UnicodeError, TypeError, AttributeError, RecursionError):
            raise InlineMediaError("provider_unavailable", 503) from None
        rows: list[dict] = []
        entries: list[tuple[str, _Entry]] = []
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get("imageinfo"), list) or not page["imageinfo"]:
                continue
            info = page["imageinfo"][0]
            if not isinstance(info, dict):
                continue
            media_mime = info.get("mime")
            wanted = "image/" if mode == "pic" else "video/"
            size, width, height = info.get("size"), info.get("width"), info.get("height")
            if (not isinstance(media_mime, str) or media_mime not in MIMES or not media_mime.startswith(wanted)
                    or type(size) is not int or not 1 <= size <= MAX_FILE_BYTES
                    or type(width) is not int or type(height) is not int
                    or not 1 <= width <= 16000 or not 1 <= height <= 16000 or width * height > 80_000_000):
                continue
            try:
                url = canonical_media_url(info.get("url"))
                source = safe_url(info.get("descriptionurl"), "source")
                preview = canonical_media_url(info.get("thumburl", "")) if info.get("thumburl") else ""
            except (InlineMediaError, ValueError):
                continue
            metadata = info.get("extmetadata", {})
            if not isinstance(metadata, dict):
                continue
            def field(key):
                value = metadata.get(key, {})
                return plain(value.get("value", "")) if isinstance(value, dict) else ""
            license_name, author = field("LicenseShortName"), field("Artist")
            # Unknown licensing is not silently staged as reusable media.
            if not license_name or not author:
                continue
            result_id = secrets.token_hex(16)
            row = {"id": result_id, "title": plain(page.get("title", ""), 160).removeprefix("File:"),
                   "author": author, "license": license_name, "source": source,
                   "mime": media_mime, "bytes": size, "width": width, "height": height,
                   "preview": bool(preview)}
            entries.append((result_id, _Entry(row, url, preview, self.clock() + RESULT_TTL_SECONDS)))
            rows.append(row)
        with self._lock:
            for result_id, entry in entries:
                self._entries[result_id] = entry
            while len(self._entries) > 48:
                self._entries.popitem(last=False)
        return {"schema": 1, "provider": PROVIDER, "mode": mode, "results": rows}

    def preview(self, result_id: object) -> tuple[bytes, str]:
        entry = self._entry(result_id)
        if not entry.preview_url:
            raise InlineMediaError("preview_unavailable", 404)
        key = entry.preview_url
        with self._lock:
            now = self.clock()
            cached = self._previews.get(key)
            if cached is not None:
                if cached[2] > now:
                    self._previews.move_to_end(key)
                    return cached[0], cached[1]
                self._previews.pop(key)
            failed = self._preview_errors.get(key)
            if failed is not None:
                if failed[0] > now:
                    delay = min(failed[3], max(1, math.ceil(failed[0] - now))) if failed[3] is not None else None
                    raise InlineMediaError(failed[1], failed[2], retry_after_seconds=delay)
                self._preview_errors.pop(key)
            future = self._preview_pending.get(key)
            producer = future is None
            if producer:
                future = Future()
                self._preview_pending[key] = future
        if not producer:
            try:
                return future.result(timeout=26)
            except FutureTimeout:
                raise InlineMediaError("preview_unavailable", 404) from None
        try:
            data, mime = self.transport(key, MAX_PREVIEW_BYTES, "media")
            if len(data) > MAX_PREVIEW_BYTES or not mime.startswith("image/") or not valid_content(data, mime):
                raise InlineMediaError("unsupported_media", 422)
            with self._lock:
                self._previews[key] = data, mime, self.clock() + RESULT_TTL_SECONDS
                while (len(self._previews) > 48
                        or sum(len(value[0]) for value in self._previews.values()) > 12 * 1024 * 1024):
                    self._previews.popitem(last=False)
            future.set_result((data, mime))
            return data, mime
        except Exception as error:
            safe = error if isinstance(error, InlineMediaError) else InlineMediaError("provider_unavailable", 503)
            with self._lock:
                lifetime = min(30, safe.retry_after_seconds or 5)
                self._preview_errors[key] = self.clock() + lifetime, safe.code, safe.status, safe.retry_after_seconds
                while len(self._preview_errors) > 48:
                    self._preview_errors.popitem(last=False)
            future.set_exception(safe)
            raise safe from None
        finally:
            with self._lock:
                self._preview_pending.pop(key, None)

    def select(self, payload: object) -> dict:
        if not isinstance(payload, dict) or set(payload) != {"id"}:
            raise InlineMediaError("invalid_input")
        entry = self._entry(payload["id"])
        # Serialize selected downloads so disk quotas cannot race.
        if not self._download_lock.acquire(blocking=False):
            raise InlineMediaError("download_busy", 409)
        try:
            with self._hold_root():
                return self._stage_entry(entry)
        except InlineMediaError:
            raise
        except OSError:
            raise InlineMediaError("storage_unavailable", 503) from None
        finally:
            self._download_lock.release()

    def _stage_entry(self, entry: _Entry) -> dict:
        temporary: Path | None = None
        try:
            from launch_mod import _check_path, _config_stream
            total = 0
            for item in self.root.iterdir():
                if not re.fullmatch(r"valmedia-[0-9a-f]{32}\.(jpg|png|gif|webp|webm|mp4|part)", item.name):
                    continue
                _check_path(item, directory=False)
                stat = item.stat()
                if time.time() - stat.st_mtime > STAGING_TTL_SECONDS:
                    try:
                        item.unlink()
                        continue
                    except OSError:
                        pass
                total += stat.st_size
            if total + entry.row["bytes"] > MAX_CACHE_BYTES:
                raise InlineMediaError("cache_full", 507)
            data, mime = self.transport(entry.url, MAX_FILE_BYTES, "media")
            if (len(data) > MAX_FILE_BYTES or len(data) != entry.row["bytes"]
                    or mime != entry.row["mime"] or not valid_content(data, mime)):
                raise InlineMediaError("unsupported_media", 422)
            suffix = MIMES[mime]
            token = secrets.token_hex(16)
            # The opaque URL is returned only after a successful close. Writing
            # the exclusively created final name keeps ancestor holds intact:
            # Windows can reject child renames under these share-mode guards.
            temporary = self.root / ("valmedia-" + token + suffix)
            with _config_stream(temporary, create=True) as stream:
                stream.write(data)
            final = temporary
            temporary = None
            caption = "Источник: Wikimedia Commons\n" + entry.row["author"] + " · " + entry.row["license"] + "\n" + entry.row["source"]
            return {"schema": 1, "provider": PROVIDER, "url": final.as_uri(), "mime": mime, "caption": caption}
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

"""MIT: bounded Platform client; paid operations require authenticated backend readback."""
import json
import re
import ssl
import urllib.error
import urllib.request
import httpx
from datetime import datetime, timezone
from urllib.parse import urlsplit

API_ORIGIN = "https://api.cuddler.ai"
MEDIA = {"video": ("videos", "vid"), "image": ("images", "img"),
         "speech": ("speech", "aud"), "music": ("music", "mus")}
JSON_LIMIT = 2 * 1024 * 1024
FILE_LIMIT = 16 * 1024 * 1024
_MISSING = object()
STATUS = frozenset(("queued", "running", "succeeded", "failed", "canceled"))
_OPERATION_404_PATHS = (
    ("POST", r"/v1/operations", "operation_prepare"),
    ("GET", r"/v1/operations/op_[A-Za-z0-9]{20}", "operation_read"),
    ("POST", r"/v1/operations/op_[A-Za-z0-9]{20}/uploads/[A-Za-z0-9_-]{1,64}", "operation_upload_checkpoint"),
    ("POST", r"/v1/operations/op_[A-Za-z0-9]{20}/execute", "operation_execute"),
    ("GET", r"/v1/uploads/upl_[A-Za-z0-9]{20}", "upload_read"),
    ("POST", r"/v1/uploads/upl_[A-Za-z0-9]{20}/complete", "upload_complete"),
)
_OPERATION_DIAGNOSTICS = frozenset(stage for _, _, stage in _OPERATION_404_PATHS)

def _api_operation(method, path):
    return next((stage for verb, pattern, stage in _OPERATION_404_PATHS
                 if method == verb and re.fullmatch(pattern, path)), None)

class PlatformError(Exception):
    """Only a fixed local code reaches callers; never server text or URLs."""
    def __init__(self, code, *, api_operation=None, http_status=None):
        self.code = code
        allowed = (code == "not_found" and type(http_status) is int and http_status == 404 and
                   isinstance(api_operation, str) and api_operation in _OPERATION_DIAGNOSTICS)
        self._api_operation = api_operation if allowed else None
        self._http_status = 404 if allowed else None
        super().__init__(code)

    @property
    def diagnostics(self):
        if (self.code == "not_found" and type(self._http_status) is int and self._http_status == 404 and
                isinstance(self._api_operation, str) and self._api_operation in _OPERATION_DIAGNOSTICS):
            return {"api_operation": self._api_operation, "http_status": 404}
        return {}

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def _default_transport(method, url, headers, body, limit):
    # Each opener explicitly disables environment/system proxies and redirects.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        stream = opener.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        stream = error
    with stream:
        length = stream.headers.get("Content-Length")
        if length is not None:
            try:
                if int(length) < 0:
                    raise ValueError()
                if int(length) > limit:
                    raise PlatformError("response_too_large")
            except ValueError:
                raise PlatformError("invalid_response") from None
        chunks = []
        size = 0
        while True:
            chunk = stream.read(min(65536, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise PlatformError("response_too_large")
            chunks.append(chunk)
        return stream.code, dict(stream.headers.items()), b"".join(chunks)

def _default_source_transport(method, url, headers, body, limit):
    """Direct native-source GET only; count raw bytes and never read error bodies."""
    if method != "GET" or body is not None or set(headers) != {"Accept"}:
        raise PlatformError("invalid_request")
    with httpx.Client(trust_env=False, follow_redirects=False, verify=True, timeout=30) as client:
        with client.stream("GET", url, headers={**headers, "Accept-Encoding": "identity"}) as response:
            normalized = _headers(dict(response.headers))
            if response.status_code != 200:
                return response.status_code, normalized, b""
            if normalized.get("content-encoding", "identity").strip().lower() != "identity":
                raise PlatformError("invalid_file")
            length = normalized.get("content-length")
            if length is not None:
                try:
                    declared = int(length)
                    if declared < 0: raise ValueError()
                except ValueError:
                    raise PlatformError("invalid_response") from None
                if declared > limit: raise PlatformError("response_too_large")
            chunks = []; size = 0
            for chunk in response.iter_raw(chunk_size=min(65536, limit + 1)):
                size += len(chunk)
                if size > limit: raise PlatformError("response_too_large")
                chunks.append(chunk)
            return response.status_code, normalized, b"".join(chunks)

def _date(value):
    try:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError()
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise PlatformError("invalid_response") from None

def _headers(value):
    if not isinstance(value, dict):
        raise PlatformError("invalid_response")
    return {str(k).lower(): str(v) for k, v in value.items()}

def _safe_fields(value, allowed):
    # No recursive pass-through of arbitrary metadata or provider text.
    return {k: value[k] for k in allowed if k in value and
            (value[k] is None or isinstance(value[k], (str, int, float, bool)))}

class Client:
    def __init__(self, api_key, *, transport=None, source_transport=None, now=None, max_file_bytes=16*1024*1024):
        if (not isinstance(api_key, str) or not api_key or len(api_key) > 4096
                or any(ord(ch) < 33 or ord(ch) > 126 for ch in api_key)):
            raise PlatformError("invalid_credentials")
        if isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int) or not 0 < max_file_bytes <= FILE_LIMIT:
            raise PlatformError("invalid_request")
        self._api_key = api_key
        self._transport = transport or _default_transport
        self._source_transport = source_transport or transport or _default_source_transport
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._max_file_bytes = max_file_bytes

    def _send(self, method, url, headers, body, limit, *, file=False, source=False):
        try:
            transport = self._source_transport if source else self._transport
            status, response_headers, data = transport(method, url, headers, body, limit)
            if isinstance(status, bool) or not isinstance(status, int) or not isinstance(data, bytes):
                raise PlatformError("invalid_response")
            normalized = _headers(response_headers)
            if source and status != 200:
                return status, normalized, b""
            length = normalized.get("content-length")
            if length is not None:
                try:
                    declared = int(length)
                    if declared < 0:
                        raise ValueError()
                except ValueError:
                    raise PlatformError("invalid_response") from None
                if declared > limit:
                    raise PlatformError("file_too_large" if file else "response_too_large")
            if len(data) > limit:
                raise PlatformError("file_too_large" if file else "response_too_large")
            if length is not None and 200 <= status < 300 and declared != len(data):
                raise PlatformError("invalid_file" if file else "invalid_response")
            return status, normalized, data
        except PlatformError as error:
            if file and error.code == "response_too_large":
                raise PlatformError("file_too_large") from None
            raise
        except Exception:
            raise PlatformError("network_error") from None

    def _api(self, method, path, body=None):
        try:
            encoded = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, OverflowError):
            raise PlatformError("invalid_request") from None
        if encoded is not None and len(encoded) > JSON_LIMIT:
            raise PlatformError("invalid_request")
        headers = {"Authorization": "Bearer " + self._api_key, "Accept": "application/json"}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        status, response_headers, data = self._send(method, API_ORIGIN + path, headers, encoded, JSON_LIMIT)
        if not 200 <= status < 300:
            code = {400: "invalid_request", 401: "authentication_failed", 403: "permission_denied",
                    404: "not_found", 409: "conflict", 429: "rate_limited"}.get(status, "api_unavailable")
            # Preserve only public 409 vocabulary; never return message/details or unknown codes.
            if status == 409 and response_headers.get("content-type", "").split(";", 1)[0].strip().lower() == "application/json":
                try:
                    error_body = json.loads(data)
                    public_code = error_body.get("code") if isinstance(error_body, dict) else None
                    if isinstance(public_code, str) and public_code in {"output_unavailable", "idempotency_conflict", "not_cancelable"}:
                        code = public_code
                except (ValueError, UnicodeError, RecursionError):
                    pass
            raise PlatformError(code, api_operation=_api_operation(method, path) if status == 404 else None, http_status=status)
        if response_headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise PlatformError("invalid_response")
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError, RecursionError):
            raise PlatformError("invalid_response") from None
        if not isinstance(value, dict):
            raise PlatformError("invalid_response")
        return value

    def _media(self, media, job_id=_MISSING):
        if not isinstance(media, str) or media not in MEDIA:
            raise PlatformError("invalid_request")
        segment, prefix = MEDIA[media]
        if job_id is not _MISSING and (not isinstance(job_id, str) or
                re.fullmatch(prefix + r"_[A-Za-z0-9]{20}", job_id) is None):
            raise PlatformError("invalid_request")
        return segment

    def validate_credentials(self):
        value = self._api("GET", "/v1/balance")
        if value.get("object") != "balance":
            raise PlatformError("invalid_response")
        return _safe_fields(value, ("object", "total_usd", "held_usd", "available_usd", "daily_spend_usd", "daily_limit_usd", "expires_at"))

    def list_models(self):
        value = self._api("GET", "/v1/models")
        if value.get("object") != "list" or not isinstance(value.get("data"), list):
            raise PlatformError("invalid_response")
        result = []
        for model in value["data"]:
            if (not isinstance(model, dict) or model.get("object") != "model"
                    or not isinstance(model.get("id"), str)
                    or not isinstance(model.get("display_name"), str)
                    or type(model.get("enabled")) is not bool):
                raise PlatformError("invalid_response")
            if model["enabled"]:
                result.append(_safe_fields(model, ("object", "id", "display_name", "kind", "enabled", "description", "price_version")))
        return {"object": "list", "data": result}

    def estimate(self, media, request_dict):
        segment = self._media(media)
        if not isinstance(request_dict, dict):
            raise PlatformError("invalid_request")
        value = self._api("POST", "/v1/" + segment + "/estimate", request_dict)
        expected = "estimate" if media == "video" else media + "_estimate"
        if value.get("object") != expected:
            raise PlatformError("invalid_response")
        return _safe_fields(value, ("object", "model", "resolution", "aspect_ratio", "duration", "input_video_seconds", "tokens", "images", "meter", "units", "usd", "usd_micros", "hold_usd", "usd_per_second", "usd_per_image", "price_version", "unit", "list_usd"))

    def _job(self, media, job_id, *, cancel=False):
        segment = self._media(media, job_id)
        path = "/v1/" + segment + "/" + job_id + ("/cancel" if cancel else "")
        value = self._api("POST" if cancel else "GET", path)
        if (value.get("object") != media or value.get("id") != job_id or
                not isinstance(value.get("status"), str) or value.get("status") not in STATUS):
            raise PlatformError("invalid_response")
        return value

    def _public_job(self, value):
        result = _safe_fields(value, ("object", "id", "model", "status", "created_at", "started_at", "completed_at", "queue_position", "price_version"))
        for key in ("estimate", "usage"):
            if isinstance(value.get(key), dict):
                result[key] = _safe_fields(value[key], ("meter", "units", "usd", "usd_micros", "hold_usd"))
        if value.get("error") is not None:
            result["error"] = {"code": "job_failed"}
        return result

    def get_job(self, media, job_id):
        return self._public_job(self._job(media, job_id))

    def cancel(self, media, job_id):
        self._media(media, job_id)
        if media == "image":
            raise PlatformError("unsupported_cancel")
        value = self._job(media, job_id, cancel=True)
        if value["status"] != "canceled":
            raise PlatformError("invalid_response")
        return self._public_job(value)

    def prepare(self, *args, **kwargs):
        from .operations import Operations
        return Operations(self).prepare(*args, **kwargs)

    def execute(self, *args, **kwargs):
        from .operations import Operations
        return Operations(self).execute(*args, **kwargs)

    def _file_url(self, url):
        try:
            if not isinstance(url, str) or len(url) > 16384 or any(ord(c) <= 32 or ord(c) == 127 for c in url):
                raise ValueError()
            parsed = urlsplit(url)
            # Exact R2 S3 host only; no alternate domains, credentials or redirect hops.
            if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                    or "#" in url or parsed.port not in (None, 443) or
                    re.fullmatch(r"[0-9a-f]{32}\.r2\.cloudflarestorage\.com", parsed.hostname or "") is None
                    or not parsed.path.startswith("/")):
                raise ValueError()
        except (ValueError, TypeError):
            raise PlatformError("unsafe_file_url") from None
        return url

    def _file_type(self, media, output, headers, content):
        mime = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        expected = {"video": ("video/mp4",), "image": ("image/jpeg", "image/png", "image/webp"),
                    "speech": ("audio/mpeg",), "music": ("audio/mpeg",)}[media]
        if mime not in expected or (media != "video" and output.get("content_type") != mime):
            raise PlatformError("invalid_file")
        valid = {"video/mp4": len(content) >= 12 and content[4:8] == b"ftyp",
                 "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
                 "image/jpeg": content.startswith(b"\xff\xd8\xff"),
                 "image/webp": len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP",
                 "audio/mpeg": content.startswith(b"ID3") or (len(content) >= 2 and content[0] == 255 and content[1] & 0xe0 == 0xe0 and content[1] & 0x06 != 0 and content[1] & 0x18 != 0x08)}[mime]
        if not valid:
            raise PlatformError("invalid_file")
        ext = {"video/mp4": "mp4", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "audio/mpeg": "mp3"}[mime]
        return mime, ext

    def artifact(self, media, job_id):
        download = self._job_download(media, job_id)
        next(download)  # Public job snapshot; standalone artifact() returns only bytes.
        return next(download)

    def _job_download(self, media, job_id):
        """One invocation owns its validated snapshot; callers cannot supply one."""
        value = self._job(media, job_id)
        yield self._public_job(value)
        for attempt in range(2):
            if attempt:
                value = self._job(media, job_id)
            output = value.get("output")
            if value["status"] != "succeeded" or not isinstance(output, dict):
                raise PlatformError("output_unavailable")
            now = self._now()
            if not isinstance(now, datetime) or now.tzinfo is None:
                raise PlatformError("invalid_request")
            if _date(output.get("retained_until")) <= now:
                raise PlatformError("output_unavailable")
            url = self._file_url(output.get("url"))
            if _date(output.get("url_expires_at")) <= now:
                if attempt == 0:
                    continue
                raise PlatformError("output_unavailable")
            status, headers, content = self._send("GET", url, {"Accept": "*/*"}, None, self._max_file_bytes, file=True)
            if status in (403, 404):
                if attempt == 0:
                    continue
                raise PlatformError("output_unavailable")
            if status != 200:
                raise PlatformError("download_failed")
            mime, extension = self._file_type(media, output, headers, content)
            yield content, mime, job_id + "." + extension
            return
        raise PlatformError("output_unavailable")

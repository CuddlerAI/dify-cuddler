"""Bounded native Dify File reads and authenticated-operation upload checkpoints."""
import hashlib
import json
import re
from urllib.parse import unquote, urlsplit

from dify_plugin.file.file import File
from .client import PlatformError, FILE_LIMIT, _date

SOURCE_HOSTS = frozenset(("cloud.dify.ai", "upload.dify.ai"))
MIMES = {"image": ("image/jpeg", "image/png", "image/webp"),
         "video": ("video/mp4", "video/quicktime"),
         "audio": ("audio/wav", "audio/x-wav", "audio/mpeg")}


def _source_url(url):
    try:
        if (not isinstance(url, str) or len(url) > 16384 or
                any(ord(c) <= 32 or ord(c) == 127 for c in url) or "#" in url):
            raise ValueError()
        parsed = urlsplit(url)
        path = unquote(parsed.path)
        if (parsed.scheme != "https" or parsed.hostname not in SOURCE_HOSTS or
                parsed.username is not None or parsed.password is not None or
                parsed.port not in (None, 443) or not parsed.path.startswith("/files/") or not path.startswith("/files/") or
                "%" in path or any(ord(c) <= 32 or ord(c) == 127 for c in path) or
                "\\" in path or any(part in (".", "..") for part in path.split("/"))):
            raise ValueError()
    except (ValueError, TypeError):
        raise PlatformError("unsafe_source_url") from None
    return url


def _bindings(files, raw):
    if files is None:
        files = []
    if not isinstance(files, list) or len(files) > 8:
        raise PlatformError("invalid_request")
    if raw is None and not files:
        return []
    try:
        if not isinstance(raw, str) or len(raw.encode()) > 8192:
            raise ValueError()
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) != len(files):
            raise ValueError()
        seen = set()
        output = []
        # Validate every URL/metadata before any source is downloaded.
        for file, binding in zip(files, values):
            if not isinstance(file, File) or not isinstance(binding, dict) or set(binding) != {"source_id", "source_version", "kind"}:
                raise ValueError()
            identity, version, kind = (binding[k] for k in ("source_id", "source_version", "kind"))
            if (not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity) or
                    identity in seen or not isinstance(version, str) or not 1 <= len(version) <= 256 or
                    not isinstance(kind, str) or kind not in MIMES or file.mime_type not in MIMES[kind] or
                    type(file.size) is not int or not 0 < file.size <= FILE_LIMIT):
                raise ValueError()
            _source_url(file.url)
            seen.add(identity)
            output.append((file, dict(binding)))
        return output
    except (ValueError, TypeError, UnicodeError):
        raise PlatformError("invalid_request") from None


def resolve(client, files, raw):
    """Use only native File metadata; File.blob is deliberately never accessed."""
    validated = _bindings(files, raw)
    records = []
    total = 0
    for file, binding in validated:
        if total + file.size > FILE_LIMIT:
            raise PlatformError("source_too_large")
        status, headers, data = client._send("GET", file.url, {"Accept": file.mime_type}, None,
                                              min(client._max_file_bytes, file.size), file=True, source=True)
        if status != 200:
            raise PlatformError("output_unavailable")
        mime = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if len(data) != file.size or mime != file.mime_type:
            raise PlatformError("source_changed")
        total += len(data)
        record = {**binding, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                  "content_type": mime}
        records.append((record, data))
    return records


def validate_upload(value, source, expected_id=None, *, now):
    if (not isinstance(value, dict) or value.get("object") != "upload" or
            not isinstance(value.get("id"), str) or not re.fullmatch(r"upl_[A-Za-z0-9]{20}", value["id"]) or
            (expected_id is not None and value["id"] != expected_id) or
            value.get("status") not in ("pending", "ready", "expired", "rejected") or
            type(value.get("bytes")) is not int or value["bytes"] <= 0 or value.get("kind") != source["kind"] or
            value.get("content_type") not in MIMES[source["kind"]]):
        raise PlatformError("invalid_operation")
    if value["status"] in ("expired", "rejected") or _date(value.get("expires_at")) <= now:
        raise PlatformError("output_unavailable")
    # Pending PUT must still match the exact signed source bytes and content type.
    # Complete may normalize image metadata/encoding. Ready identity/kind, expiry
    # and the signed source->upload checkpoint bind that normalized object.
    if value["status"] == "pending" and any(value.get(k) != source[k] for k in ("content_type", "bytes")):
        raise PlatformError("invalid_operation")
    return value


def transfer(client, value, source, data):
    """No key changes or unbounded retries; unknown complete responses query the original upload."""
    upload = validate_upload(value, source, now=client._now())
    if upload["status"] == "ready":
        return upload
    if _date(upload.get("expires_at")) <= client._now():
        raise PlatformError("output_unavailable")
    url = client._file_url(upload.get("upload_url"))
    original = upload.get("upload_headers")
    if not isinstance(original, dict):
        raise PlatformError("invalid_operation")
    headers = {str(k).lower(): str(v) for k, v in original.items()}
    if (set(headers) - {"content-type", "content-length"} or
            headers.get("content-type") != source["content_type"] or
            headers.get("content-length") != str(len(data))):
        raise PlatformError("invalid_operation")
    # A PUT response cannot prove complete/ready. The fixed ID is checkpointed by the API first.
    try:
        status, _, _ = client._send("PUT", url, headers, data, 65536, file=True)
        if status not in (200, 201, 204):
            raise PlatformError("output_unavailable")
    except PlatformError as error:
        if error.code != "network_error":
            raise
        # Query the original ID only. If pending, the caller can rerun prepare with the same slot.
        current = validate_upload(client._api("GET", "/v1/uploads/" + upload["id"]), source, upload["id"], now=client._now())
        if current["status"] != "ready":
            raise PlatformError("upload_unknown") from None
        return current
    try:
        result = client._api("POST", "/v1/uploads/" + upload["id"] + "/complete")
    except PlatformError as error:
        if error.code != "network_error":
            raise
        result = client._api("GET", "/v1/uploads/" + upload["id"])
    result = validate_upload(result, source, upload["id"], now=client._now())
    if result["status"] != "ready":
        raise PlatformError("upload_unknown")
    return result

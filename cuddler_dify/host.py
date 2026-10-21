"""Native SDK tools: backend-authenticated receipts and named workflow outputs."""
import json
from .client import Client, PlatformError


def _download(parameters):
    value = parameters.get("download", False)
    if type(value) is not bool:
        raise PlatformError("invalid_request")
    return value


def invoke(tool, name, parameters):
    try:
        if not isinstance(parameters, dict):
            raise PlatformError("invalid_request")
        client = Client(tool.runtime.credentials.get("api_key"))
        media = parameters.get("media")
        if name == "list_models":
            result = client.list_models()
        elif name == "prepare_operation":
            result = client.prepare(**parameters)
            yield tool.create_json_message(result)
            yield tool.create_variable_message("operation_id",result["operation_id"])
            yield tool.create_variable_message("operation_ref",result["operation_ref"])
            return
        elif name == "execute_operation":
            download = _download(parameters)
            result = client.execute(**{k:v for k,v in parameters.items() if k != "download"})
            yield tool.create_json_message(result)
            yield tool.create_variable_message("job_id",result["job_id"])
            yield tool.create_variable_message("operation_ref",result["operation_ref"])
            if download and result["job"].get("status") == "succeeded":
                blob,mime,filename=client.artifact(result["job"]["object"],result["job_id"])
                yield tool.create_blob_message(blob,{"mime_type":mime,"filename":filename})
            return
        elif name == "estimate":
            raw = parameters.get("request_json")
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
                raise PlatformError("invalid_request")
            def reject_constant(_):
                raise ValueError()
            try:
                body = json.loads(raw, parse_constant=reject_constant)
            except (ValueError, TypeError):
                raise PlatformError("invalid_request") from None
            if not isinstance(body, dict):
                raise PlatformError("invalid_request")
            result = client.estimate(media, body)
        elif name == "get_job":
            download = _download(parameters)
            job_id = parameters.get("job_id")
            stream = client._job_download(media, job_id) if download else None
            result = next(stream) if download else client.get_job(media, job_id)
            yield tool.create_json_message(result)
            yield tool.create_variable_message("job_id",result["id"])
            if download:
                blob, mime, filename = next(stream)
                yield tool.create_blob_message(blob, {"mime_type": mime, "filename": filename})
            return
        elif name == "cancel_job":
            result = client.cancel(media, parameters.get("job_id"))
        else:
            raise PlatformError("invalid_request")
        yield tool.create_json_message(result)
    except PlatformError as error:
        yield tool.create_json_message({"object": "error", "code": error.code, **error.diagnostics})
    except Exception:
        yield tool.create_json_message({"object": "error", "code": "platform_request_failed"})

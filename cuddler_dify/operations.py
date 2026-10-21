"""Exact-text signed operation plans backed by Platform atomic storage, never Dify KV."""
import hashlib
import hmac
import json
import math
import re

from .client import API_ORIGIN, MEDIA, PlatformError, _date
from . import sources

CONTRACT = "cuddler.cloud-recovery/1"
ENVELOPE = "cuddler.platform-operation/1"
REF_KEYS = {"id", "contract", "envelope_version", "plan_sha256", "signature"}


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(raw, limit=65536, code="invalid_request"):
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > limit:
            raise ValueError()
        def reject(_):
            raise ValueError()
        result = json.loads(raw, parse_constant=reject)
        def finite(value):
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError()
            if isinstance(value, dict):
                for item in value.values(): finite(item)
            elif isinstance(value, list):
                for item in value: finite(item)
        finite(result)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise PlatformError(code) from None


def _identifier(value, prefix):
    return isinstance(value, str) and re.fullmatch(prefix + r"_[A-Za-z0-9]{20}", value) is not None


def _hex(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _equal(left, right):
    return isinstance(left, str) and isinstance(right, str) and hmac.compare_digest(left.encode(), right.encode())


def _mac(key, value):
    return hmac.new(key, value.encode("utf-8"), "sha256").hexdigest()


def _reference(view):
    return json.dumps({k: view[k] for k in ("id", "contract", "envelope_version", "plan_sha256", "signature")}, separators=(",", ":"))


def _placeholders(value):
    result = set()
    if isinstance(value, dict):
        if "source_id" in value:
            if set(value) != {"source_id"} or not isinstance(value["source_id"], str):
                raise PlatformError("invalid_request")
            result.add(value["source_id"])
        elif "upload_id" in value:
            raise PlatformError("invalid_request")
        else:
            for item in value.values(): result.update(_placeholders(item))
    elif isinstance(value, list):
        for item in value: result.update(_placeholders(item))
    return result


def _validate_final(template, final, upload_ids=None):
    """Compare parsed structures, while MAC/digest always cover exact original UTF-8 text."""
    mapping = {} if upload_ids is None else dict(upload_ids)
    def compare(left, right):
        if isinstance(left, dict):
            if set(left) == {"source_id"}:
                source_id = left["source_id"]
                if not _identifier(right, "upl"):
                    return False
                if source_id in mapping and mapping[source_id] != right:
                    return False
                mapping[source_id] = right
                return True
            return isinstance(right, dict) and left.keys() == right.keys() and all(compare(v, right[k]) for k, v in left.items())
        if isinstance(left, list):
            return isinstance(right, list) and len(left) == len(right) and all(compare(a,b) for a,b in zip(left,right))
        return type(left) is type(right) and left == right
    if not compare(template, final):
        raise PlatformError("invalid_operation")
    return mapping


class Operations:
    def __init__(self, client):
        self.client = client
        digest = hashlib.sha256(client._api_key.encode()).digest()
        self.signing_key = hmac.digest(digest, b"cuddler.platform-operation/signing/1", "sha256")

    def verify(self, view, *, reference=None, expected=None, upload_ids=None):
        try:
            if (not isinstance(view, dict) or view.get("object") != "operation" or
                    not _identifier(view.get("id"), "op") or view.get("contract") != CONTRACT or
                    view.get("envelope_version") != ENVELOPE or type(view.get("revision")) is not int or view["revision"] < 1 or
                    view.get("state") not in ("prepared", "send_started", "unknown", "job_known") or
                    not _hex(view.get("plan_sha256")) or not _hex(view.get("signature"))):
                raise ValueError()
            raw = view.get("plan_json")
            plan = _json(raw, 256*1024, "invalid_operation")
            if not _equal(_sha(raw), view["plan_sha256"]) or not _equal(_mac(self.signing_key, raw), view["signature"]):
                raise ValueError()
            if reference is not None and any(not _equal(reference.get(k), view[k]) for k in REF_KEYS):
                raise ValueError()
            target = plan.get("target")
            if (plan.get("contract") != CONTRACT or plan.get("envelope_version") != ENVELOPE or
                    plan.get("operation_id") != view["id"] or not isinstance(target, dict) or
                    target.get("media") not in MEDIA or target.get("origin") != API_ORIGIN or
                    target.get("method") != "POST" or target.get("path") != "/v1/" + MEDIA[target["media"]][0]):
                raise ValueError()
            slot = plan.get("slot")
            if (not isinstance(slot,dict) or set(slot) != {"logical_node","operation_batch","item_id"} or
                    any(not isinstance(v,str) or not 1 <= len(v) <= 128 for v in slot.values())):
                raise ValueError()
            credential = plan.get("credential")
            if (not isinstance(credential, dict) or not _identifier(credential.get("org_id"), "org") or
                    not _identifier(credential.get("key_id"), "key") or
                    not _equal(plan.get("credential_binding"), _mac(self.signing_key, "credential:" + credential["org_id"] + ":" + credential["key_id"]))):
                raise ValueError()
            request = _json(plan.get("request_json"), code="invalid_operation")
            template = _json(plan.get("template_json"), 256*1024, "invalid_operation")
            budget = {"present": "max_usd" in request}
            if "max_usd" in request: budget["max_usd"] = request["max_usd"]
            if plan.get("budget") != budget:
                raise ValueError()
            _date(plan.get("created_at")); _date(plan.get("expires_at"))
            if not isinstance(plan.get("generation_key"), str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", plan["generation_key"]):
                raise ValueError()
            source_list = plan.get("sources")
            if not isinstance(source_list, list) or len(source_list) > 8:
                raise ValueError()
            for item in source_list:
                if (not isinstance(item, dict) or not isinstance(item.get("source_id"), str) or
                        re.fullmatch(r"[A-Za-z0-9_-]{1,64}",item["source_id"]) is None or
                        not isinstance(item.get("upload_key"), str) or re.fullmatch(r"[A-Za-z0-9._:-]{8,128}",item["upload_key"]) is None or
                        not _hex(item.get("sha256")) or item.get("kind") not in sources.MIMES or
                        item.get("content_type") not in sources.MIMES[item["kind"]] or
                        type(item.get("bytes")) is not int or not 0 < item["bytes"] <= sources.FILE_LIMIT or
                        not isinstance(item.get("source_version"),str) or not 1 <= len(item["source_version"]) <= 256):
                    raise ValueError()
            identities = [item["source_id"] for item in source_list]
            if (len(set(identities)) != len(identities) or len({item["upload_key"] for item in source_list}) != len(source_list) or
                    _placeholders(template) != set(identities)):
                raise ValueError()
            if expected is not None:
                if (target["media"] != expected["media"] or plan["request_json"] != expected["request_json"] or
                        plan.get("slot") != {k:expected[k] for k in ("logical_node", "operation_batch", "item_id")} or
                        sorted(({k:v for k,v in item.items() if k != "upload_key"} for item in source_list),key=lambda item:item["source_id"]) !=
                        sorted(expected["sources"],key=lambda item:item["source_id"])):
                    raise ValueError()
            final_fields = [view.get(k) for k in ("final_request_json", "final_request_sha256", "final_signature")]
            if any(item is not None for item in final_fields):
                if (not all(isinstance(item,str) for item in final_fields) or not _hex(final_fields[1]) or not _hex(final_fields[2]) or
                        not _equal(_sha(final_fields[0]),final_fields[1]) or
                        not _equal(_mac(self.signing_key,"final:" + view["id"] + ":" + final_fields[1]),final_fields[2])):
                    raise ValueError()
                _validate_final(template,_json(final_fields[0],256*1024,"invalid_operation"),upload_ids)
            if view.get("job_id") is not None:
                if not _identifier(view["job_id"],MEDIA[target["media"]][1]) or view["state"] != "job_known":
                    raise ValueError()
            elif view["state"] == "job_known":
                raise ValueError()
            return plan
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError, PlatformError):
            raise PlatformError("invalid_operation") from None

    def read(self, view, **kwargs):
        self.verify(view, **kwargs)
        reference = {k:view[k] for k in REF_KEYS}
        current = self.client._api("GET","/v1/operations/"+view["id"])
        self.verify(current,reference=reference,**{k:v for k,v in kwargs.items() if k != "reference"})
        if current["revision"] < view["revision"] or (view.get("job_id") and current.get("job_id") != view["job_id"]):
            raise PlatformError("invalid_operation")
        return current

    def prepare(self, media=None, request_json=None, logical_node=None, operation_batch=None, item_id=None,
                files=None, file_bindings_json=None, **unknown):
        if unknown:
            raise PlatformError("invalid_request")
        self.client._media(media)
        request = _json(request_json)
        for value in (logical_node,operation_batch,item_id):
            if not isinstance(value,str) or not 1 <= len(value) <= 128:
                raise PlatformError("invalid_request")
        records = sources.resolve(self.client,files,file_bindings_json)
        if _placeholders(request) != {record["source_id"] for record,data in records}:
            raise PlatformError("invalid_request")
        expected = {"contract":CONTRACT,"envelope_version":ENVELOPE,"media":media,"request_json":request_json,
                    "logical_node":logical_node,"operation_batch":operation_batch,"item_id":item_id,
                    "sources":[record for record,data in records]}
        view = self.client._api("POST","/v1/operations",expected)
        view = self.read(view,expected=expected)
        # Only authenticated final text may identify an existing source upload.
        # read() verifies both the original response and durable readback;
        # verify() also binds the signed sources to the current native inputs.
        plan = self.verify(view,expected=expected)
        upload_ids = (_validate_final(_json(plan["template_json"],256*1024,"invalid_operation"),
                                     _json(view["final_request_json"],256*1024,"invalid_operation"))
                      if view.get("final_request_json") is not None else {})
        for source,data in records:
            checkpoint={"plan_sha256":view["plan_sha256"],"signature":view["signature"],
                        "observed_sha256":source["sha256"],"action":"reserve"}
            path="/v1/operations/"+view["id"]+"/uploads/"+source["source_id"]
            original_id=upload_ids.get(source["source_id"])
            if original_id is not None:
                existing=self.client._api("GET","/v1/uploads/"+original_id)
                existing=sources.validate_upload(existing,source,original_id,now=self.client._now())
                upload=sources.transfer(self.client,existing,source,data)
            else:
                reserved=self.client._api("POST",path,checkpoint)
                if not isinstance(reserved.get("operation"),dict):
                    raise PlatformError("invalid_operation")
                self.verify(reserved["operation"],reference={k:view[k] for k in REF_KEYS},expected=expected)
                upload=sources.transfer(self.client,reserved.get("upload"),source,data)
            upload_ids[source["source_id"]]=upload["id"]
            ready=self.client._api("POST",path,{**checkpoint,"action":"ready"})
            sources.validate_upload(ready.get("upload"),source,upload["id"],now=self.client._now())
            view=self.read(ready.get("operation"),expected=expected,upload_ids=upload_ids)
        if view.get("final_request_json") is None:
            raise PlatformError("operation_not_ready")
        return {"object":"operation_receipt","operation_id":view["id"],"operation_ref":_reference(view),"state":view["state"]}

    def execute(self, operation_ref=None, **unknown):
        if unknown:
            raise PlatformError("invalid_request")
        reference=_json(operation_ref,2048,"invalid_operation")
        if (set(reference) != REF_KEYS or not _identifier(reference.get("id"),"op") or reference.get("contract") != CONTRACT or
                reference.get("envelope_version") != ENVELOPE or not _hex(reference.get("plan_sha256")) or not _hex(reference.get("signature"))):
            raise PlatformError("invalid_operation")
        view=self.client._api("GET","/v1/operations/"+reference["id"])
        plan=self.verify(view,reference=reference)
        if view.get("job_id") is None:
            if view.get("final_request_json") is None:
                raise PlatformError("operation_not_ready")
            receipt={k:view[k] for k in ("plan_sha256","signature","final_request_sha256","final_signature")}
            prior=view
            view=self.client._api("POST","/v1/operations/"+reference["id"]+"/execute",receipt)
            self.verify(view,reference=reference)
            if (view["revision"] < prior["revision"] or view.get("final_request_sha256") != prior["final_request_sha256"] or
                    view.get("job_id") is None):
                raise PlatformError("invalid_operation")
        job=self.client.get_job(plan["target"]["media"],view["job_id"])
        return {"object":"operation_result","operation_id":view["id"],"operation_ref":_reference(view),"job_id":view["job_id"],"job":job}

# Privacy and data handling

## Credentials

The plugin receives a Cuddler Platform API key through Dify's secret-input credential interface. It sends that key only as authorization to the fixed HTTPS origin `https://api.cuddler.ai`. File uploads/downloads and native source reads do not receive the Platform authorization header. This plugin does not supply its own credential storage or a per-key spending limit.

## Requests and signed operation plans

Requests may include prompts, text, generation settings, and reference media. Operation preparation sends the exact request, stable operation slot, source version/content hashes, sizes, and bindings to Cuddler. The backend retains the signed plan, including prompt/request content, for the organization's lifecycle and deletes it when that organization is deleted. A seven-day execution deadline is not automatic plan deletion. A signature or digest authenticates content; it is not plan encryption.

The selected generation service/provider processes the submitted content through Cuddler. Review the applicable [Cuddler privacy policy](https://cuddler.ai/privacy) and service terms before providing personal or confidential content. Dify and the selected model provider have their own retention and access policies.

## Files and network destinations

Native input files are read only from the exact Dify Cloud hosts `cloud.dify.ai` and `upload.dify.ai`, under `/files/`. Their temporary signed URLs are used only for the bounded source read. The plugin checks raw byte length and MIME, rejects redirects and encoded responses, and uses TLS verification and request timeouts. Validated source bytes may then be uploaded to the exact Cloudflare R2 S3 host returned by Cuddler, without the Platform API key.

Downloaded outputs are checked for expected MIME, minimum file signature, and byte limits before being returned as native Dify files. Their contents enter Dify's storage, display, and workflow processing paths. These checks are not a full media malware scanner or decoder.

## Outputs and logging

Public task outputs exclude prompts, arbitrary metadata, API key identifiers, provider error bodies, and signed output URLs. The named `operation_ref` includes an operation identifier, version, digest, and authentication tag. Restrict access to that recovery receipt and to workflow history. Fixed local error codes are returned; only six known operations/upload 404 stages may include a fixed `api_operation` and `http_status` diagnostic.

The plugin adds no analytics and does not intentionally log credentials, prompts, signed URLs, or raw errors. This does not guarantee that Dify, its SDK/daemon, workflow history, Cuddler, or model providers never log or retain data. Their access controls, backups, retention, and deletion policies apply separately. Runtime memory is not a secure-erasure guarantee.

Expired or unavailable outputs do not cause automatic replacement generation. The plugin has no event/webhook receiver and does not claim event replay or event-storage guarantees.

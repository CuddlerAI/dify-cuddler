# Cuddler for Dify

Use Cuddler video, image, speech, and music capabilities in Dify workflows. This plugin provides model discovery, cost estimates, signed operation plans, task recovery, native output files, and cancellation of supported queued tasks.

- Version: **0.3.6**
- Author namespace: `cuddler-ai`
- Source repository: https://github.com/CuddlerAI/dify-cuddler
- Support/contact: https://github.com/CuddlerAI/dify-cuddler/issues
- API: https://api.cuddler.ai
- Privacy: [PRIVACY.md](PRIVACY.md)
- License: [MIT](LICENSE); Cuddler names and artwork remain trademarks of their owners.

## Install and configure

Install the `.difypkg` from this repository's GitHub Releases using Dify's plugin installation dialog. Marketplace installation becomes available only after the Marketplace review is approved.

Configure an existing Cuddler Platform API key in the plugin's credential form. Key validation performs a read-only balance request and does not create a generation task. A live key and sufficient organization balance are required for operation preparation and generation. Store the key only in Dify's credential form; do not place it in workflow outputs or prompts. The key may authorize spending against the organization balance; this plugin does not add a separate per-key spending limit.

The plugin runs on Python 3.12 with `dify-plugin==0.10.2`. Outbound TLS access is required to `api.cuddler.ai`, Dify Cloud native-file hosts, and the exact Cloudflare R2 S3 host returned by Cuddler for authorized uploads/downloads. Custom API origins and redirect hops are rejected.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_models` | Read available models without generation |
| `estimate` | Quote a video/image/speech/music request without generation |
| `prepare_operation` | Persist a credential-bound signed plan and optional native-file upload mappings; does not create a generation task |
| `execute_operation` | Execute or recover the original logical operation; returns the original task when already known |
| `get_job` | Query an existing task and optionally return a native Dify output file |
| `cancel_job` | Cancel a supported queued video, speech, or music task; image cancellation is unsupported |

## Workflow setup

1. Discover models and estimate the intended request.
2. Call `prepare_operation` with a media type, `request_json`, and stable `logical_node`, `operation_batch`, and `item_id` values. Keep these stable for retries of the same intent; do not use an execution ID, item index, or list position.
3. Persist the returned **named `operation_ref` variable** in durable workflow/application state before the paid step. Pass that exact value to `execute_operation`; callers cannot replace backend persistence with a `verified` or `plan_persisted` flag.
4. Resume with the same original reference and credentials. Changing the request, budget, source version/content, or credential is rejected before paid submission. A new intended generation requires an explicitly new operation slot.
5. Poll the named `job_id` through `get_job`. Enable `download` to deliver a successful output as a native Dify file. An expired or unavailable output does not trigger a replacement generation.

`request_json.max_usd` must be a decimal **string**, for example `"0.100000"`, rather than a JSON number. Supply the model's other parameters according to the Platform API. Estimates are not final charges, and retrying with a different operation slot can create another billable task.

### Native input files

Bind a native Dify File array to `files`, with an equally sized ordered `file_bindings_json` array:

```json
[{"source_id":"reference","source_version":"immutable-v1","kind":"image"}]
```

Use `{"source_id":"reference"}` in the matching media input, such as an entry in `reference_images`. Supported source kinds are image, audio, and video. Reference audio/video clips must meet Cuddler's 2–15 second duration rule. Ready uploads are recovered by their original signed mapping rather than recreated.

Only native source URLs on `cloud.dify.ai/files/` and `upload.dify.ai/files/` are accepted. The plugin reads bounded raw bytes, checks MIME/type/length, and applies a 16 MiB per-file and total-source bound. This is a plugin protection limit, not the general Platform API upload limit. Output file delivery is also bounded to 16 MiB.

## Release status and limitations

The runtime in this release matches the 0.3.6 Dify Cloud candidate. Release preparation updates documentation only. Dify Cloud installation, read-only model/estimate workflows, native image/audio/video source uploads, independent operation recovery, credential/request/budget/source-change rejection, and successful video/music native output delivery have real Cloud evidence. Existing image/speech tasks were also delivered as native files; they were not newly generated in that run.

The maintainer elected to release this candidate without further acceptance runs. Real queued cancellation was not established: the test target had already completed and returned `not_cancelable`. First-response-loss and expired-link fault handling have automated synthetic/isolated coverage, not production Cloud fault-injection evidence. No event/webhook receiver is implemented; recovery uses polling. Do not interpret a host workflow's `SUCCESS` status as proof that an individual tool's business result succeeded; inspect its error/status fields.

Dify Community Edition has not been verified. Its custom native-file hosts are not accepted by the current Cloud-only source allowlist. Large outputs over the plugin bound, arbitrary URLs, custom API endpoints, and event replay are unsupported.

# Cross-Channel Sandbox Document Delivery Architecture

## Overview
Vigil's sandbox harness includes two dedicated document delivery tools in `backend/src/modules/runs/tools/delivery.py`:
- `deliver_file`: Copies a file generated inside the ephemeral container (`/workspace/...`) to the host's delivery directory and computes its byte size and SHA-256 fingerprint.
- `generate_document`: Renders structured documents (`markdown`, `text`, `html`, `csv`) directly into the host delivery directory and returns an `artifact_ref`.

In the Web Chat UI (`ThinkingCapsule.tsx` / `SandboxDocumentCard`), tool calls and results for `deliver_file` and `generate_document` are paired into interactive **Document Artifact Cards** that remain pinned below the thinking summary even when the step trace is collapsed.

## Future Consideration: Universal Chat Interface Delivery

When extending Vigil so that sandbox documents can be delivered seamlessly to **any** connected chat interface (Web Chat, Mobile, Slack, Telegram, Discord, Email connectors), the following pipeline should be used:

### 1. Automatic Artifact Registration in `RunLoop`
Currently, `backend/src/modules/artifacts/router.py` exposes:
- `POST /api/v1/artifacts` (registers a run artifact row in the database)
- `GET /api/v1/artifacts/{artifact_id}` (metadata)
- `GET /api/v1/artifacts/{artifact_id}/download` (streams binary file with `Content-Disposition: attachment`)
- `GET /api/v1/artifacts/{artifact_id}/text` (extracts the document body as text for PDF, DOCX, XLSX and text files)
- `GET /api/v1/artifacts/run/{run_id}` (lists all artifacts for a run)

In `RunLoop._write_tool_row` (`backend/src/modules/runs/loop.py`), whenever a tool returns a `ToolResult` with a non-null `artifact_ref`:
1. Persist an `ArtifactCreateDTO` record tied to `run.id` with `kind="delivery"`, `path=artifact_ref`, `size_bytes`, and an optional text `preview`.
2. Include `artifact_id` and `download_url: f"/api/v1/artifacts/{artifact_id}/download"` inside the `tool_result` step event payload.

### 2. Channel Adapter Dispatch
For external chat interfaces (or outbound connector actions):
- **Web & Mobile Native Clients:** Consume `artifact_id` from the `tool_result` step payload or `GET /api/v1/artifacts/run/{run_id}` and stream the binary file via authenticated `fetch` against `/api/v1/artifacts/{artifact_id}/download`.
- **External Messaging Connectors (Slack / Telegram / Email):** Subscribe to completed runs or expose a `send_artifact` connector capability that reads the registered `ArtifactDTO` stream from `ArtifactsRepository.get_artifact_stream(artifact_id)` and uploads it directly to the target thread or channel API.

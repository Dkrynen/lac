# LAC Public Readiness Roadmap

This is a practical roadmap, not a shipped-feature inventory. Treat every item
below as a work lane until the app, tests, docs, and public copy prove it.

## Current public-readiness gate

LAC is public-ready only when a new Windows user can install it, find or import a
model, use the model locally, understand failures, and recover without Duan
walking them through the app.

Gate checks:

- Clean Windows install opens the app from the packaged installer.
- At least one small, known-good local model path completes end to end.
- Import UX stays GGUF-first and guided; safetensors conversion remains a
  power-user path, not the happy path.
- Hugging Face gated/private flows keep tokens local and surface actionable
  auth errors.
- Progress, current file, bytes, cancel, retry, and failure labels are visible
  for long-running import/download work.
- Docs and launch copy describe only features visible in the current app.
- `model-hub` does not import `lac_pro`; Pro remains local/private.
- No public push, publish, installer release, or store listing happens without
  Duan's explicit approval.

## Competitor takeaways

| Competitor | Researched anchor | Takeaway for LAC |
|---|---|---|
| LM Studio | Local/private model running, model discovery, local server, SDK/MCP/headless deployment | Local privacy and discovery are table stakes. Developer/runtime surfaces are a serious lane, but should not be marketed until built and verified. |
| Jan | Open-source ChatGPT replacement, local/open/cloud models, memory | LAC can lean into ownership and Windows-native reliability. Memory is a future lane only if it is opt-in, local, inspectable, and deletable. |
| Open WebUI | Offline-capable provider-agnostic platform with Ollama/OpenAI-compatible APIs, RAG, tool calling, plugins, MCP/OpenAPI, admin/evals | The market rewards extensibility. LAC should pick a narrow Windows desktop entry point first, then add modular APIs, tools, and admin/eval surfaces deliberately. |
| AnythingLLM | Zero-setup private RAG, agents, document ingestion, local defaults | Private document workflows need near-zero setup, clear ingestion state, and local defaults. RAG should feel operational, not like a research demo. |
| GPT4All | Private desktop local models and LocalDocs | Private desktop use plus local documents remains a durable public story if the setup and failure recovery are clean. |

## Next feature lanes

1. Windows-first reliability
   - Installer, launch, update, uninstall, logs, crash recovery, and clean
     default settings.
   - Produce a repeatable smoke script plus a human checklist for a clean
     Windows machine.

2. Model discovery and import
   - GGUF-first browse/import flow with compatibility labels and clear source
     choices.
   - Keep Ollama and Hugging Face paths explicit; gated/private Hugging Face
     needs local token handling and recovery text.
   - Keep safetensors conversion available but clearly marked as advanced.

3. Local model use flow
   - Audit the current end-to-end path from "I have no model" to "I used a
     local model successfully."
   - Close gaps in loading state, model state, logs, stop/cancel behavior, and
     plain-language errors before adding broad new surfaces.

4. Local server and developer integration
   - Evaluate OpenAI-compatible local server controls, SDK entry points,
     headless launch, MCP, and API docs as one lane.
   - Ship only behind verified controls and docs that match the app.

5. Private RAG and LocalDocs
   - Start with local file/folder ingestion, index status, source citations,
     reindex/delete controls, and clear storage location.
   - Default to local processing. Any cloud/provider option must be explicit.

6. Agents, tools, plugins, and admin/evals
   - Build from the existing plugin direction instead of inventing a second
     extension system.
   - Treat tool calling, MCP/OpenAPI, admin settings, and eval runs as advanced
     workflows after the core local model path is stable.

7. Public docs and positioning
   - Create public copy from verified app behavior, not competitor checklists.
   - Keep roadmap language separate from shipped-feature language.
   - Include Windows setup, model import, privacy, troubleshooting, and Pro
     boundary notes.

## Subagent roles/prompts

Run subagents in parallel only when their file ownership does not overlap. Every
subagent must list intended files before editing and must avoid unrelated dirty
worktree changes.

**Readiness gatekeeper**

Prompt: "Inspect the current LAC app, installer docs, README, tests, and UI/API
surfaces. Produce a public-readiness gap table with columns: area, current
evidence, blocker, owner, verification command. Do not edit files."

**Competitor mapper**

Prompt: "Using the researched anchors in `docs/PUBLIC_READINESS_ROADMAP.md`,
translate competitor signals into LAC priorities and copy risks. Do not add live
web research unless Duan asks. Mark every recommendation as shipped, partial,
missing, or roadmap."

**Windows QA operator**

Prompt: "Run the Windows smoke path from packaged install to first successful
local model use. Capture exact commands, app logs, screenshots if useful,
failures, and retry behavior. Do not change product code."

**Import UX builder**

Prompt: "Work only on assigned import UX/backend files. Improve the GGUF-first
flow, source resolution, token-aware Hugging Face behavior, progress/cancel/retry
labels, and actionable failure states. Add or update focused tests."

**Developer integration builder**

Prompt: "Audit current local API/server capabilities and propose the smallest
verified OpenAI-compatible, SDK/headless, or MCP-facing increment. Implement only
the assigned increment and document exact user controls."

**Private RAG builder**

Prompt: "Design the smallest local document workflow for LAC: file/folder
ingest, indexing state, retrieval result citations, delete/reindex controls, and
privacy defaults. Do not claim RAG exists until a verified path is implemented."

**Release docs editor**

Prompt: "Compare public docs, README, changelog, screenshots, and app UI. Remove
or rewrite unsupported claims. Separate shipped behavior from roadmap language.
Produce a launch checklist for Duan approval."

## Done criteria

Public readiness is done when:

- A clean Windows machine can install, launch, and complete one verified local
  model use path.
- Import/download failure paths are actionable and recoverable.
- The public docs match the app exactly, including limitations.
- Any local server, RAG, MCP, memory, agents, plugins, admin, or eval wording is
  either verified in-app behavior or clearly labeled as roadmap.
- Relevant backend, frontend, packaging, and smoke checks pass with commands
  recorded.
- The Pro/open-core boundary remains intact.
- Duan has explicitly approved the release/publish step.

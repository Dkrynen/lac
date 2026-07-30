# Local AI First

**The case for running your coding agent on your own hardware — and why the gap was never the model.**

---

## The assumption

The default narrative in 2026: serious AI coding work requires cloud models.
Claude, GPT, Gemini — the big APIs. Local models are toys. Fine for a chatbot,
not for real agent work.

This narrative is wrong. Not slightly wrong. Fundamentally wrong.

## The proof

On a mid-range AMD rig — RX 6800 XT (16 GB VRAM), Ryzen 5 7600, 32 GB RAM,
no data-centre GPU, no H100, no cloud subscription — a local 20B-parameter
model did the following through a full OpenCode agent loop:

1. Received a coding task
2. Used the `read` tool to open a Python file
3. Reasoned about what happens when `summarize([])` is called
4. Returned the exact correct answer: `ZeroDivisionError`

At **94.67 tokens per second**. No cloud calls. No API key. No rate limit.
No third party logging the prompt. The code never left the machine.

This was not a cherry-picked demo. It was a bounded eval: three trials,
three arms (raw Ollama, stock OpenCode, LAC-tuned variant), scored by exact
text match. The stock OpenCode arm passed **2 out of 3 trials**. The LAC
agent variant passed **1 out of 3** — and the failures were model
consistency at temperature 1.0, not capability failures.

The model can do the work. The question was never "can local models reason
about code?" They can. The question was "does the tooling around them let
them?"

## The gap

It was never the model. It was everything around it.

**Context window.** The agent loop needs a large context window to hold the
system prompt, tool definitions, and conversation history. Point a stock local
model at OpenCode with its default 4k context and the tool calls collapse. The
model literally cannot hold the conversation. Ollama truncates the input prompt
at roughly half of `num_ctx`, so a 32k window only buys ~16k of usable prompt
— not enough for a single real file read. LAC raises the context window to
65k (yielding ~32k of usable prompt) before the agent ever starts.

**Model selection.** "Use the biggest model that fits" is not a strategy.
Agent work requires tool-calling capability — a specific architectural
property, not a parameter count. A 30B model without native tool-calling
support will flail where a 20B model with it will succeed. LAC's recommender
scores for tool-calling capability, not just size.

**Hardware fit.** A model that fits in VRAM on paper may not fit in practice
once the KV cache, the agent loop overhead, and the OS display driver are
accounted for. LAC scans the actual hardware — GPU, VRAM, RAM, CPU, iGPU —
and recommends models that fit the real machine, not the spec sheet.

**The tuned variant.** The difference between "runs" and "runs well" is the
tuning. GPU offload layers, RAM spill thresholds, iGPU offload on APUs. LAC
Pro sweeps these automatically. The free tier raises the context window;
Pro makes the model fast on your specific rig.

## The product

LAC is the hardware brain for local AI agents.

```bash
lac agent .
```

One command. LAC scans your hardware, picks the best agent-capable model
that fits, prepares a context-raised variant, configures OpenCode to point
at it, and launches. No cloud. No API key. No configuration file to write
by hand.

The free tier is complete and stays free:

- Hardware scan (Windows, macOS, Linux)
- Agent-capable model recommendation with 65k context floor
- Context-raised model variant (baked once, reused)
- OpenCode configuration and launch
- `/scan` and `/recommend` inside the agent session

**Pro** ($36/year) adds the tuning cockpit:

- GPU offload sweep
- RAM spill configuration
- iGPU offload on APUs
- Calibration history and regression detection
- Private Hugging Face imports

## Why this matters

**Privacy.** Your code is your intellectual property. Every prompt sent to a
cloud API is a copy of your codebase held by a third party. Local inference
means the code never leaves your machine. Not "encrypted in transit." Never
leaves.

**Cost.** Cloud API bills scale with usage. A heavy agentic coding day on
Claude or GPT can cost $50–$90. The same day on a local model costs
electricity. The model is a one-time download. The inference is free forever.

**Sovereignty.** Cloud APIs change pricing, deprecate models, rate-limit
you, and log your prompts. A local model is yours. It does not get deprecated.
It does not get rate-limited. It does not send your code to a server you do
not control.

**Latency.** 94.67 tok/s on a mid-range AMD GPU. No network round-trip. No
queue. No cold start. The model is already loaded.

## The honest caveat

Local models are not cloud models. A 20B local model will not match Claude
Opus on a complex multi-file refactor. It will not match GPT-4 on a novel
architecture design task.

But most coding work is not that. Most coding work is: read a file, understand
it, make a targeted change, run the tests. A well-configured local model does
this reliably, at zero marginal cost, with your code staying on your machine.

The right question is not "can local models do everything cloud models can?"
It is "can local models do the work I actually do, on my hardware, for free?"

For most developers, most of the time: yes.

**Local AI first. Cloud when you genuinely need it.**

---

*Evidence: LAC eval run 2026-07-28, gpt-oss:20b on RX 6800 XT (16 GB),*
*Ryzen 5 7600, 32 GB RAM. Diagnostic mode. Three trials, three arms.*
*Stock OpenCode: 2/3 passed. LAC agent variant: 1/3 passed.*
*Raw Ollama: 2/3 passed, 94.67 tok/s.*

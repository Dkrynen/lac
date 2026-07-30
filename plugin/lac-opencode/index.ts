import { type Plugin, tool } from "@opencode-ai/plugin"
import { execSync } from "child_process"

const LAC_CLI: string[] = process.env.LAC_CLI ? [process.env.LAC_CLI] : ["lac"]

const SAFE_ARG = /^[A-Za-z0-9._:/-]+$/

function lacCommand(parts: string[]): string {
  return [...LAC_CLI, ...parts].map((part) => '"' + part + '"').join(" ")
}

function run(parts: string[]): string {
  try {
    return execSync(lacCommand(parts), { encoding: "utf-8", timeout: 300_000 })
  } catch (e) {
    return `LAC command failed (${parts.join(" ")}): ${e}`
  }
}

function safeArg(value: string): string {
  if (!SAFE_ARG.test(value)) {
    throw new Error(`Unsafe argument for LAC: ${value}`)
  }
  return value
}

export const LacOpencodePlugin: Plugin = async (ctx) => {
  return {
    tool: {
      lac_scan: tool({
        description: "Scan this machine's hardware (GPU, VRAM, RAM, CPU) and return the LAC report",
        args: {},
        async execute() {
          return run(["scan"])
        },
      }),
      lac_recommend: tool({
        description: "Recommend the best agent-capable local model for this machine using LAC",
        args: {},
        async execute() {
          return run(["recommend", "--use-case", "agent"])
        },
      }),
      lac_status: tool({
        description: "Show LAC Pro license and tuning-cockpit status",
        args: {},
        async execute() {
          return run(["pro", "status"])
        },
      }),
      lac_tune: tool({
        description: "LAC Pro: sweep GPU-offload configs for a model and apply the fastest to this rig",
        args: { model: tool.schema.string() },
        async execute(args) {
          return run(["pro", "tune", "--apply", safeArg(args.model)])
        },
      }),
      lac_benchmark: tool({
        description: "LAC Pro: benchmark a model's tok/s via Ollama on this machine",
        args: { model: tool.schema.string() },
        async execute(args) {
          return run(["pro", "benchmark", safeArg(args.model)])
        },
      }),
      lac_insights: tool({
        description: "LAC Pro: calibration history and speed-regression detection for this machine",
        args: {},
        async execute() {
          return run(["pro", "insights"])
        },
      }),
      lac_import: tool({
        description: "LAC Pro: import a compatible model from Hugging Face by repo id (org/model)",
        args: { repo: tool.schema.string() },
        async execute(args) {
          return run(["pro", "import", safeArg(args.repo)])
        },
      }),
    },
  }
}

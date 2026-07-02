import { toast } from "sonner";
import { api } from "@/lib/api";

/** Track active pulls so they can be cancelled from the UI. */
const activePulls = new Map<string, AbortController>();

/**
 * Pull a model from Ollama via the streaming /api/ollama/pull endpoint,
 * surfacing live progress as a Sonner toast with a Cancel button.
 * Calls onDone when complete (not when cancelled).
 */
export function pullWithToast(model: string, onDone?: () => void) {
  // If already pulling this model, ignore duplicate.
  if (activePulls.has(model)) return;

  const controller = new AbortController();
  activePulls.set(model, controller);

  const id = toast.loading(`Pulling ${model}…`, {
    action: {
      label: "Cancel",
      onClick: () => controller.abort(),
    },
  });

  let pct = 0;

  (async () => {
    try {
      for await (const ev of api.pull(model, controller.signal)) {
        if (ev.error) throw new Error(String(ev.error));
        const c = Number(ev.completed ?? 0);
        const t = Number(ev.total ?? 0);
        const status = String(ev.status ?? "");
        if (t > 0) {
          pct = Math.max(pct, Math.round((c / t) * 100));
          toast.loading(`Pulling ${model} — ${pct}%`, {
            id,
            description: status,
            action: {
              label: "Cancel",
              onClick: () => controller.abort(),
            },
          });
        } else {
          toast.loading(`Pulling ${model}…`, {
            id,
            description: status,
            action: {
              label: "Cancel",
              onClick: () => controller.abort(),
            },
          });
        }
      }
      toast.success(`Installed ${model}`, { id });
      onDone?.();
    } catch (e) {
      if (controller.signal.aborted) {
        toast.info(`Cancelled pull of ${model}`, { id });
      } else {
        toast.error(`Failed to pull ${model}`, {
          id,
          description: e instanceof Error ? e.message : String(e),
        });
      }
    } finally {
      activePulls.delete(model);
    }
  })();
}

/** Cancel all active pulls (e.g. on page unload). */
export function cancelAllPulls() {
  for (const controller of activePulls.values()) {
    controller.abort();
  }
  activePulls.clear();
}

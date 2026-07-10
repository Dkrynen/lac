import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Bot,
  Code2,
  Compass,
  FileText,
  FolderOpen,
  Hammer,
  MessageSquare,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  User,
} from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Markdown } from "@/components/markdown";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import type { PsResponse, SessionDetail, SessionEvent, SessionMessage, SessionSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

type Msg = SessionMessage;
type Mode = "ask" | "plan" | "explore" | "build";

interface ChatStats {
  ttft_ms?: number;
  load_ms?: number;
  prompt_ms?: number;
  eval_ms?: number;
  eval_count?: number;
  tokens_per_second?: number;
}

interface WorkbenchEvent {
  type: string;
  name?: string;
  ok?: boolean;
  args?: unknown;
  result?: string;
  message?: string;
  timestamp?: number;
}

const SUGGESTIONS = [
  "Map the next files to inspect before changing code.",
  "Find the safest implementation path for this project.",
  "Review the current workspace and summarize risks.",
  "Draft a small build plan with verification steps.",
];

const MODES: { id: Mode; label: string; icon: typeof MessageSquare; disabled?: boolean }[] = [
  { id: "ask", label: "Ask", icon: MessageSquare },
  { id: "plan", label: "Plan", icon: FileText },
  { id: "explore", label: "Explore", icon: Compass },
  { id: "build", label: "Build", icon: Hammer, disabled: true },
];

const PROJECT_ROOT_KEY = "lac.workbench.projectRoot";
const WORKBENCH_SESSION_LIMIT = 80;

export function Chat() {
  const [params] = useSearchParams();
  const installed = useAsync(() => api.installed());
  const config = useAsync(() => api.config());
  const workspaces = useAsync(() => api.workspaces());
  const running = useAsync<PsResponse>(() => api.ps().catch(() => ({ running: false, models: [] })));

  const models = useMemo(() => (installed.data ?? []).map((m) => m.name), [installed.data]);
  const runningModels = useMemo(() => new Set((running.data?.models ?? []).map((m) => m.name)), [running.data]);

  const [workspace, setWorkspace] = useState("");
  const activeWorkspace = workspace || config.data?.workspace || "default";
  const sessions = useAsync(() => api.sessions(activeWorkspace, WORKBENCH_SESSION_LIMIT), [activeWorkspace]);

  const [model, setModel] = useState(params.get("model") ?? "");
  const [mode, setMode] = useState<Mode>("plan");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [events, setEvents] = useState<WorkbenchEvent[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [input, setInput] = useState("");
  const [system, setSystem] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [warming, setWarming] = useState(false);
  const [lastStats, setLastStats] = useState<ChatStats | null>(null);
  const [projectRoot, setProjectRoot] = useState(() => localStorage.getItem(PROJECT_ROOT_KEY) ?? "");
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!workspace && config.data?.workspace) setWorkspace(config.data.workspace);
  }, [config.data?.workspace, workspace]);

  useEffect(() => {
    if (projectRoot.trim()) localStorage.setItem(PROJECT_ROOT_KEY, projectRoot.trim());
    else localStorage.removeItem(PROJECT_ROOT_KEY);
  }, [projectRoot]);

  useEffect(() => {
    if (model) return;
    const configured = config.data?.default_model;
    if (configured && models.includes(configured)) setModel(configured);
    else if (models.length) setModel(models[0]);
  }, [config.data?.default_model, model, models]);

  useEffect(() => {
    if (!model) return;
    let cancelled = false;
    setWarming(true);
    api.warm(model, true)
      .then((res) => {
        if (cancelled) return;
        if (res?.state === "failed") {
          toast.error("Model warm-up failed", { description: res.error ?? "Ollama did not load the model." });
        }
      })
      .finally(() => {
        if (!cancelled) setWarming(false);
      });
    return () => {
      cancelled = true;
    };
  }, [model]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const visibleSessions = sessions.data ?? [];
  const selectedSession = visibleSessions.find((s) => s.id === activeSessionId);

  const loadSession = async (id: string) => {
    try {
      const detail = await api.session(id);
      const split = splitSystem(detail);
      setActiveSessionId(detail.id);
      setMessages(split.messages);
      setSystem(split.system);
      setEvents((detail.events ?? []).map(eventFromStored));
      setLastStats(null);
      if (detail.model) setModel(detail.model);
      if (detail.workspace) setWorkspace(detail.workspace);
    } catch (e) {
      toast.error("Could not load session", { description: e instanceof Error ? e.message : String(e) });
    }
  };

  const newSession = () => {
    setActiveSessionId("");
    setMessages([]);
    setEvents([]);
    setLastStats(null);
  };

  const switchWorkspace = (next: string) => {
    setWorkspace(next);
    setActiveSessionId("");
    setMessages([]);
    setEvents([]);
    api.switchWorkspace(next).catch((e) => {
      toast.error("Could not switch workspace", { description: e instanceof Error ? e.message : String(e) });
    });
  };

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!model) {
      toast.error("Select a model first");
      return;
    }
    if (!trimmed || streaming || warming) return;
    if (mode === "build") {
      toast.info("Build mode needs approval controls first");
      return;
    }

    const prior = transcriptWithSystem(system, messages);
    const assistantIndex = messages.length + 1;
    const initialAssistant = mode === "ask" ? "" : `${modeLabel(mode)} agent starting...`;
    setMessages([...messages, { role: "user", content: trimmed }, { role: "assistant", content: initialAssistant }]);
    setInput("");
    setStreaming(true);
    setLastStats(null);
    if (mode !== "ask") {
      setEvents((prev) => [...prev, { type: "run", name: mode, message: trimmed, timestamp: Date.now() / 1000 }]);
    }

    const ac = new AbortController();
    abortRef.current = ac;

    try {
      if (mode === "ask") {
        await streamPlainChat(trimmed, prior, assistantIndex, ac.signal);
      } else {
        await streamAgentChat(trimmed, prior, assistantIndex, ac.signal);
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        toast.error("Workbench error", { description: e instanceof Error ? e.message : String(e) });
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
      sessions.reload();
    }
  };

  const streamPlainChat = async (
    text: string,
    prior: Msg[],
    assistantIndex: number,
    signal: AbortSignal
  ) => {
    const history = [...prior, { role: "user", content: text }];
    let acc = "";
    const startedAt = performance.now();
    let ttftMs: number | undefined;

    for await (const ev of api.chat(model, history as { role: string; content: string }[], signal)) {
      if (ev.error) throw new Error(String(ev.error));
      const message = ev.message as { content?: string; thinking?: string } | undefined;
      const delta = message?.content ?? "";
      const thinking = message?.thinking ?? "";
      if (thinking && !acc) {
        ttftMs ??= performance.now() - startedAt;
        replaceAssistant(assistantIndex, "Thinking...");
      }
      if (delta) {
        ttftMs ??= performance.now() - startedAt;
        acc += delta;
        replaceAssistant(assistantIndex, acc);
      }
      if (ev.done === true) {
        setLastStats(chatStatsFromEvent(ev, ttftMs));
      }
    }
  };

  const streamAgentChat = async (
    text: string,
    prior: Msg[],
    assistantIndex: number,
    signal: AbortSignal
  ) => {
    let acc = "";
    const agent = mode === "explore" ? "explore" : "plan";

    for await (const ev of api.agentChat(
      {
        agent,
        model,
        message: text,
        messages: prior,
        session_id: activeSessionId || undefined,
        workspace: activeWorkspace,
        cwd: projectRoot.trim() || undefined,
        name: selectedSession?.name || text.slice(0, 64),
      },
      signal
    )) {
      const type = String(ev.type ?? "");
      if (type === "session" && typeof ev.session_id === "string") {
        setActiveSessionId(ev.session_id);
      } else if (type === "status") {
        const event = eventFromRaw(ev);
        setEvents((prev) => [...prev, event]);
        if (!acc) replaceAssistant(assistantIndex, `${event.message || "Agent started"}...`);
      } else if (type === "delta") {
        acc += String(ev.content ?? "");
        replaceAssistant(assistantIndex, acc);
      } else if (type === "tool_call" || type === "tool_result" || type === "tool_calls") {
        setEvents((prev) => [...prev, eventFromRaw(ev)]);
      } else if (type === "done") {
        acc = String(ev.content ?? acc);
        replaceAssistant(assistantIndex, acc);
      } else if (type === "error") {
        const event = eventFromRaw(ev);
        setEvents((prev) => [...prev, event]);
        throw new Error(event.message || "Agent run failed");
      }
    }
  };

  const replaceAssistant = (index: number, content: string) => {
    setMessages((prev) => {
      const next = [...prev];
      next[index] = { role: "assistant", content };
      return next;
    });
  };

  const stop = () => abortRef.current?.abort();
  const clear = () => {
    setMessages([]);
    setEvents([]);
    setLastStats(null);
  };

  return (
    <>
      <PageHeader title="Workbench" className="mb-3">
        <Button variant="ghost" size="sm" onClick={newSession}>
          <MessageSquare /> New
        </Button>
        <Button variant="ghost" size="sm" onClick={clear} disabled={!messages.length && !events.length}>
          <Trash2 /> Clear
        </Button>
      </PageHeader>

      <div className="grid min-h-[520px] grid-cols-1 gap-3 xl:h-[calc(100vh-150px)] xl:grid-cols-[270px_minmax(0,1fr)_320px]">
        <aside className="flex min-h-[320px] flex-col overflow-hidden rounded-lg border border-line bg-panel xl:min-h-0">
          <div className="border-b border-line p-3">
            <div className="mb-2 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
              <FolderOpen className="h-3.5 w-3.5" /> Workspace
            </div>
            {workspaces.loading ? (
              <Skeleton className="h-8 w-full" />
            ) : (
              <Select value={activeWorkspace} onValueChange={switchWorkspace}>
                <SelectTrigger className="h-8">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(workspaces.data ?? []).map((w) => (
                    <SelectItem key={w.id} value={w.id}>
                      {w.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>

          <div className="border-b border-line p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                <Code2 className="h-3.5 w-3.5" /> Project
              </div>
              <Badge variant="outline">Root</Badge>
            </div>
            <Input
              value={projectRoot}
              onChange={(e) => setProjectRoot(e.target.value)}
              placeholder="C:\\Users\\User\\repos\\model-hub"
              className="h-8 text-[12.5px]"
            />
          </div>

          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-center justify-between border-b border-line px-3 py-2">
              <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                Sessions
              </span>
              <Button variant="ghost" size="sm" className="h-7 px-2" onClick={newSession}>
                New
              </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {sessions.loading ? (
                <div className="space-y-2">
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                </div>
              ) : visibleSessions.length ? (
                <div className="space-y-1.5">
                  {visibleSessions.map((s) => (
                    <SessionRow
                      key={s.id}
                      session={s}
                      active={s.id === activeSessionId}
                      onClick={() => loadSession(s.id)}
                    />
                  ))}
                </div>
              ) : (
                <div className="px-2 py-8 text-center text-[12.5px] text-fg-muted">No saved sessions</div>
              )}
            </div>
          </div>
        </aside>

        <section className="flex min-h-[520px] flex-col overflow-hidden rounded-lg border border-line bg-panel xl:min-h-0">
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <span className="text-[12px] uppercase tracking-[0.08em] text-fg-faint">Model</span>
              {installed.loading ? (
                <Skeleton className="h-8 w-48" />
              ) : models.length ? (
                <Select value={model} onValueChange={setModel}>
                  <SelectTrigger className="h-8 w-full max-w-[280px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {models.map((m) => (
                      <SelectItem key={m} value={m}>
                        {m}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <span className="text-[13px] text-fg-muted">No models installed</span>
              )}
              {warming && <Badge variant="info" dot>Warming</Badge>}
              {model && runningModels.has(model) && <Badge variant="success" dot>Resident</Badge>}
            </div>

            <div className="flex items-center gap-1 rounded border border-line bg-panel-2 p-0.5">
              {MODES.map((item) => (
                <ModeButton
                  key={item.id}
                  mode={item}
                  active={mode === item.id}
                  onClick={() => setMode(item.id)}
                />
              ))}
            </div>
          </div>

          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto p-4">
            {messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center text-center">
                <Sparkles className="mb-3 h-7 w-7 text-verdant" />
                <div className="grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => send(s)}
                      className="min-h-[54px] rounded-lg border border-line bg-panel-2 px-3 py-2 text-left text-[13px] text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="mx-auto max-w-4xl space-y-5">
                {messages.map((m, i) => (
                  <Bubble key={`${m.role}-${i}`} role={m.role} content={m.content} model={model} />
                ))}
              </div>
            )}
          </div>

          <div className="border-t border-line p-3">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                send(input);
              }}
              className="flex items-end gap-2"
            >
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={model ? `Message ${modeLabel(mode)} with ${model}` : "Install a model to start"}
                disabled={!model || streaming || warming}
                rows={2}
                className="min-h-[44px] flex-1 resize-none rounded border border-line bg-panel-2 px-3 py-2 text-[14px] text-fg outline-none placeholder:text-fg-faint focus:border-line-strong disabled:cursor-not-allowed disabled:opacity-60"
              />
              {streaming ? (
                <Button type="button" variant="secondary" onClick={stop}>
                  <Square /> Stop
                </Button>
              ) : (
                <Button type="submit" disabled={!model || warming || !input.trim() || mode === "build"}>
                  <Send /> Send
                </Button>
              )}
            </form>
          </div>
        </section>

        <aside className="flex min-h-[360px] flex-col overflow-hidden rounded-lg border border-line bg-panel xl:min-h-0">
          <div className="border-b border-line p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                <ShieldCheck className="h-3.5 w-3.5" /> Run
              </div>
              <Badge variant={mode === "build" ? "warning" : mode === "ask" ? "neutral" : "accent"}>
                {modeLabel(mode)}
              </Badge>
            </div>
            <div className="grid grid-cols-2 gap-2 text-[12.5px]">
              <StatTile label="Session" value={activeSessionId ? shortId(activeSessionId) : "New"} />
              <StatTile label="Events" value={String(events.length)} />
            </div>
          </div>

          <div className="border-b border-line p-3">
            <div className="mb-2 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
              <Settings2 className="h-3.5 w-3.5" /> System
            </div>
            <textarea
              value={system}
              onChange={(e) => setSystem(e.target.value)}
              placeholder="Optional system prompt"
              rows={4}
              className="w-full resize-none rounded border border-line bg-panel-2 px-3 py-2 text-[13px] text-fg outline-none placeholder:text-fg-faint focus:border-line-strong"
            />
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <div className="mb-2 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
              <Bot className="h-3.5 w-3.5" /> Events
            </div>
            {events.length ? (
              <div className="space-y-2">
                {events.slice().reverse().map((event, i) => (
                  <RunEvent key={`${event.type}-${i}`} event={event} />
                ))}
              </div>
            ) : (
              <div className="rounded border border-dashed border-line px-3 py-8 text-center text-[12.5px] text-fg-muted">
                No agent events
              </div>
            )}
          </div>

          <div className="border-t border-line p-3">
            <div className="mb-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
              Stats
            </div>
            {lastStats ? (
              <div className="grid grid-cols-2 gap-2">
                <StatTile label="TTFT" value={formatMs(lastStats.ttft_ms) ?? "-"} />
                <StatTile label="Speed" value={lastStats.tokens_per_second ? `${Math.round(lastStats.tokens_per_second)} tok/s` : "-"} />
              </div>
            ) : (
              <div className="text-[12.5px] text-fg-muted">No stats for this run</div>
            )}
          </div>
        </aside>
      </div>
    </>
  );
}

function ModeButton({
  mode,
  active,
  onClick,
}: {
  mode: { id: Mode; label: string; icon: typeof MessageSquare; disabled?: boolean };
  active: boolean;
  onClick: () => void;
}) {
  const Icon = mode.icon;
  return (
    <button
      type="button"
      disabled={mode.disabled}
      onClick={onClick}
      className={cn(
        "flex h-8 items-center gap-1.5 rounded px-2 text-[12.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        active ? "bg-panel text-fg shadow-sm" : "text-fg-muted hover:bg-panel-3 hover:text-fg"
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      <span>{mode.label}</span>
    </button>
  );
}

function SessionRow({
  session,
  active,
  onClick,
}: {
  session: SessionSummary;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full rounded border px-2.5 py-2 text-left transition-colors",
        active ? "border-verdant bg-verdant-soft/40" : "border-line bg-panel-2 hover:border-line-strong"
      )}
    >
      <div className="truncate text-[13px] font-medium text-fg">{session.name || "Untitled"}</div>
      <div className="mt-1 flex items-center justify-between gap-2 text-[11.5px] text-fg-faint">
        <span className="truncate">{session.model || "No model"}</span>
        <span className="shrink-0">{formatDate(session.updated_at)}</span>
      </div>
    </button>
  );
}

function Bubble({ role, content, model }: { role: string; content: string; model: string }) {
  const user = role === "user";
  const Icon = user ? User : Bot;
  return (
    <div className={cn("flex gap-3", user && "flex-row-reverse")}>
      <div
        className={cn(
          "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
          user ? "bg-verdant text-verdant-fg" : "bg-panel-3 text-fg-muted"
        )}
      >
        <Icon className="h-4 w-4" />
      </div>
      <div className={cn("min-w-0 max-w-[84%]", user && "text-right")}>
        <div className={cn("mb-1 text-[11px] text-fg-faint", user && "hidden")}>{model}</div>
        <div
          className={cn(
            "rounded-lg px-3.5 py-2.5 text-[14px]",
            user ? "bg-verdant text-verdant-fg" : "bg-panel-2 text-fg"
          )}
        >
          {user ? content : <Markdown text={content || "..."} />}
        </div>
      </div>
    </div>
  );
}

function RunEvent({ event }: { event: WorkbenchEvent }) {
  const ok = event.ok;
  const title = event.name || event.type;
  const body = event.result || event.message || (event.args ? JSON.stringify(event.args) : "");
  return (
    <div className="rounded border border-line bg-panel-2 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="truncate text-[12.5px] font-medium text-fg">{title}</div>
        {typeof ok === "boolean" && (
          <Badge variant={ok ? "success" : "danger"}>{ok ? "OK" : "Fail"}</Badge>
        )}
      </div>
      {body && (
        <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-fg-muted">
          {body}
        </pre>
      )}
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-line bg-panel-2 px-2.5 py-2">
      <div className="text-[11px] uppercase tracking-[0.08em] text-fg-faint">{label}</div>
      <div className="mt-1 truncate text-[13px] font-medium text-fg">{value}</div>
    </div>
  );
}

function splitSystem(detail: SessionDetail): { system: string; messages: Msg[] } {
  const messages = detail.messages ?? [];
  const systemMessage = messages.find((m) => m.role === "system");
  return {
    system: systemMessage?.content || detail.system_prompt || "",
    messages: messages.filter((m) => m.role !== "system"),
  };
}

function transcriptWithSystem(system: string, messages: Msg[]): Msg[] {
  const prompt = system.trim();
  return prompt ? [{ role: "system", content: prompt }, ...messages] : [...messages];
}

function eventFromStored(event: SessionEvent): WorkbenchEvent {
  return eventFromRaw({ type: event.type, ...event.payload, timestamp: event.timestamp });
}

function eventFromRaw(raw: Record<string, unknown>): WorkbenchEvent {
  return {
    type: String(raw.type ?? "event"),
    name: typeof raw.name === "string" ? raw.name : undefined,
    ok: typeof raw.ok === "boolean" ? raw.ok : undefined,
    args: raw.args,
    result: typeof raw.result === "string" ? raw.result : undefined,
    message: typeof raw.message === "string" ? raw.message : undefined,
    timestamp: typeof raw.timestamp === "number" ? raw.timestamp : undefined,
  };
}

function nsToMs(value: unknown): number | undefined {
  const n = Number(value ?? 0);
  if (!Number.isFinite(n) || n <= 0) return undefined;
  return n / 1_000_000;
}

function chatStatsFromEvent(ev: Record<string, unknown>, ttftMs?: number): ChatStats {
  const evalMs = nsToMs(ev.eval_duration);
  const evalCount = Number(ev.eval_count ?? 0);
  const tokensPerSecond = evalMs && evalCount > 0 ? (evalCount / evalMs) * 1000 : undefined;
  return {
    ttft_ms: ttftMs,
    load_ms: nsToMs(ev.load_duration),
    prompt_ms: nsToMs(ev.prompt_eval_duration),
    eval_ms: evalMs,
    eval_count: evalCount > 0 ? evalCount : undefined,
    tokens_per_second: tokensPerSecond,
  };
}

function formatMs(ms: number | undefined): string | null {
  if (!ms || !Number.isFinite(ms)) return null;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function formatDate(value: number): string {
  if (!value) return "-";
  return new Date(value * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

function modeLabel(mode: Mode): string {
  if (mode === "ask") return "Ask";
  if (mode === "explore") return "Explore";
  if (mode === "build") return "Build";
  return "Plan";
}

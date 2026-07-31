import { useEffect, useMemo, useRef, useState } from "react";
import { Bot, MessageSquare, Plus, Send, Settings2, Sparkles, Square, Trash2, User } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Markdown } from "@/components/markdown";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import type { PsResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  error?: boolean;
}

interface StoredSession {
  id: string;
  name: string;
  model: string;
  systemPrompt: string;
  messages: ChatMessage[];
  updatedAt: number;
}

const SESSIONS_KEY = "lac.chat.sessions.v1";
const CURRENT_KEY = "lac.chat.current.v1";

const SUGGESTIONS = [
  "Explain what this model is good at.",
  "Give me a quick Python one-liner to rename files by date.",
  "Summarize the trade-offs between 7B and 20B local models.",
  "Help me draft a commit message for a bugfix.",
];

function loadSessions(): StoredSession[] {
  try {
    const raw = localStorage.getItem(SESSIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as StoredSession[]) : [];
  } catch {
    return [];
  }
}

function persistSessions(sessions: StoredSession[]) {
  try {
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
  } catch {
    /* storage full or unavailable - chat keeps working in-memory */
  }
}

function newSessionId(): string {
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export function Chat() {
  const installed = useAsync(() => api.installed());
  const config = useAsync(() => api.config());
  const running = useAsync<PsResponse>(() => api.ps().catch(() => ({ running: false, models: [] })));

  const modelNames = useMemo(
    () => (installed.data ?? []).map((m) => m.name),
    [installed.data]
  );

  const [model, setModel] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [sessions, setSessions] = useState<StoredSession[]>(loadSessions);
  const [currentId, setCurrentId] = useState<string | null>(
    () => localStorage.getItem(CURRENT_KEY) || null
  );

  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const warmedRef = useRef<string | null>(null);

  // Default model: config default when installed, else first installed model.
  useEffect(() => {
    if (model || modelNames.length === 0) return;
    const preferred = config.data?.default_model;
    setModel(preferred && modelNames.includes(preferred) ? preferred : modelNames[0]);
  }, [model, modelNames, config.data]);

  // Warm the selected model off the send path so the first reply is fast.
  useEffect(() => {
    if (!model || warmedRef.current === model) return;
    warmedRef.current = model;
    api.warm(model, false).catch(() => undefined);
  }, [model]);

  // Restore the current session once the list is loaded from storage.
  useEffect(() => {
    if (!currentId || sessions.length === 0) return;
    const s = sessions.find((x) => x.id === currentId);
    if (!s) return;
    setMessages(s.messages);
    if (s.model) setModel(s.model);
    setSystemPrompt(s.systemPrompt);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId]);

  // Autosave the conversation.
  useEffect(() => {
    if (!currentId) return;
    setSessions((prev) => {
      const firstUser = messages.find((m) => m.role === "user");
      const name = firstUser
        ? firstUser.content.slice(0, 48) + (firstUser.content.length > 48 ? "…" : "")
        : "New chat";
      const existing = prev.find((s) => s.id === currentId);
      const updated: StoredSession = {
        id: currentId,
        name,
        model,
        systemPrompt,
        messages,
        updatedAt: Date.now(),
      };
      const next = existing
        ? prev.map((s) => (s.id === currentId ? updated : s))
        : [updated, ...prev];
      persistSessions(next);
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, model, systemPrompt, currentId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const runningNames = new Set((running.data?.models ?? []).map((m) => m.name));

  function startSession() {
    abortRef.current?.abort();
    setStreaming(false);
    setMessages([]);
    setInput("");
    const id = newSessionId();
    setCurrentId(id);
    try {
      localStorage.setItem(CURRENT_KEY, id);
    } catch {
      /* ignore */
    }
  }

  function selectSession(id: string) {
    if (id === "new") {
      startSession();
      return;
    }
    abortRef.current?.abort();
    setStreaming(false);
    setCurrentId(id);
    try {
      localStorage.setItem(CURRENT_KEY, id);
    } catch {
      /* ignore */
    }
    const s = sessions.find((x) => x.id === id);
    if (s) {
      setMessages(s.messages);
      if (s.model && modelNames.includes(s.model)) setModel(s.model);
      setSystemPrompt(s.systemPrompt);
    }
  }

  function deleteSession(id: string) {
    setSessions((prev) => {
      const next = prev.filter((s) => s.id !== id);
      persistSessions(next);
      return next;
    });
    if (currentId === id) {
      setMessages([]);
      setCurrentId(null);
      try {
        localStorage.removeItem(CURRENT_KEY);
      } catch {
        /* ignore */
      }
    }
  }

  async function send(text?: string) {
    const content = (text ?? input).trim();
    if (!content || streaming) return;
    if (!model) {
      toast.error("Select a model first.");
      return;
    }
    if (!currentId) {
      const id = newSessionId();
      setCurrentId(id);
      try {
        localStorage.setItem(CURRENT_KEY, id);
      } catch {
        /* ignore */
      }
    }
    setInput("");
    const history: ChatMessage[] = [...messages, { role: "user", content }];
    setMessages([...history, { role: "assistant", content: "" }]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;
    const payload = [
      ...(systemPrompt.trim() ? [{ role: "system", content: systemPrompt.trim() }] : []),
      ...history.map((m) => ({ role: m.role, content: m.content })),
    ];

    try {
      for await (const ev of api.chat(model, payload, controller.signal)) {
        const err = ev.error;
        if (typeof err === "string" && err) {
          toast.error(err);
          setMessages((prev) => {
            const copy = [...prev];
            copy[copy.length - 1] = { role: "assistant", content: err, error: true };
            return copy;
          });
          break;
        }
        const msg = ev.message as { content?: string } | undefined;
        const piece = msg?.content;
        if (typeof piece === "string" && piece) {
          setMessages((prev) => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            copy[copy.length - 1] = {
              role: "assistant",
              content: (last?.content ?? "") + piece,
            };
            return copy;
          });
        }
      }
    } catch (e) {
      if ((e as Error)?.name !== "AbortError") {
        const detail = (e as Error)?.message ?? String(e);
        toast.error(`Chat failed: ${detail}`);
        setMessages((prev) => {
          const copy = [...prev];
          copy[copy.length - 1] = { role: "assistant", content: detail, error: true };
          return copy;
        });
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  const sortedSessions = useMemo(
    () => [...sessions].sort((a, b) => b.updatedAt - a.updatedAt),
    [sessions]
  );

  if (installed.loading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-9 w-56" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100vh-8.5rem)] flex-col">
      <PageHeader
        title="Chat"
        subtitle="Talk to a model running on this machine — nothing leaves your rig."
      >
        {model && runningNames.has(model) && (
          <Badge variant="success">warm in VRAM</Badge>
        )}
        <Button variant="outline" size="sm" onClick={startSession}>
          <Plus className="mr-1 h-3.5 w-3.5" /> New chat
        </Button>
      </PageHeader>

      {modelNames.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center rounded-lg border border-dashed border-line bg-panel/40 p-12 text-center">
          <Bot className="mb-3 h-8 w-8 text-fg-faint" />
          <p className="text-sm font-medium">No local models installed</p>
          <p className="mt-1 max-w-sm text-[13px] text-fg-muted">
            Install a model from Browse, or run <code>lac pull qwen3:8b</code>, then come back.
          </p>
        </div>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Select value={model} onValueChange={setModel} disabled={streaming}>
              <SelectTrigger className="h-9 w-64">
                <SelectValue placeholder="Select model" />
              </SelectTrigger>
              <SelectContent>
                {modelNames.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                    {runningNames.has(name) ? "  ·  running" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={currentId ?? "new"} onValueChange={selectSession}>
              <SelectTrigger className="h-9 w-56">
                <SelectValue placeholder="Sessions" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="new">New chat</SelectItem>
                {sortedSessions.map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {currentId && (
              <Button
                variant="ghost"
                size="sm"
                title="Delete this session"
                onClick={() => deleteSession(currentId)}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            )}

            <details className="ml-auto">
              <summary className="flex cursor-pointer items-center gap-1 text-[12px] text-fg-muted hover:text-fg">
                <Settings2 className="h-3.5 w-3.5" /> System prompt
              </summary>
              <textarea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                placeholder="Optional system prompt"
                rows={2}
                className="mt-2 w-80 resize-none rounded border border-line bg-panel-2 px-2 py-1.5 text-[13px] text-fg outline-none placeholder:text-fg-faint focus:border-line-strong"
              />
            </details>

            {messages.length > 0 && !streaming && (
              <Button variant="ghost" size="sm" onClick={() => setMessages([])}>
                Clear
              </Button>
            )}
          </div>

          <div className="flex-1 space-y-4 overflow-y-auto rounded-lg border border-line bg-panel p-4">
            {messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center text-center">
                <Bot className="mb-3 h-8 w-8 text-fg-faint" />
                <p className="text-sm font-medium">Ask your local model anything</p>
                <div className="mt-4 grid max-w-lg grid-cols-1 gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => send(s)}
                      className="rounded-lg border border-line bg-panel-2 px-3 py-2 text-left text-[12.5px] text-fg-muted transition-colors hover:border-verdant hover:text-fg"
                    >
                      <Sparkles className="mr-1 inline h-3 w-3 text-verdant" />
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((m, i) => (
                <div key={i} className={cn("flex gap-3", m.role === "user" && "flex-row-reverse")}>
                  <div
                    className={cn(
                      "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border",
                      m.role === "user"
                        ? "border-verdant/40 bg-verdant/10 text-verdant"
                        : "border-line bg-panel-2 text-fg-muted"
                    )}
                  >
                    {m.role === "user" ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
                  </div>
                  <div
                    className={cn(
                      "max-w-[80%] rounded-lg border px-3.5 py-2.5 text-[13.5px] leading-relaxed",
                      m.role === "user"
                        ? "border-verdant/30 bg-verdant/5"
                        : m.error
                          ? "border-red-500/40 bg-red-500/5 text-red-400"
                          : "border-line bg-panel-2"
                    )}
                  >
                    {m.role === "assistant" ? (
                      m.content ? (
                        <Markdown text={m.content} />
                      ) : (
                        <span className="inline-flex items-center gap-1.5 text-fg-faint">
                          <MessageSquare className="h-3.5 w-3.5 animate-pulse" /> generating…
                        </span>
                      )
                    ) : (
                      <span className="whitespace-pre-wrap">{m.content}</span>
                    )}
                  </div>
                </div>
              ))
            )}
            <div ref={endRef} />
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
            className="mt-3 flex items-end gap-2"
          >
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              rows={2}
              placeholder={model ? `Message ${model}  (Enter to send, Shift+Enter for newline)` : "Select a model to start chatting"}
              disabled={!model}
              className="min-h-[48px] flex-1 resize-none rounded-lg border border-line bg-panel-2 px-3 py-2.5 text-[14px] text-fg outline-none placeholder:text-fg-faint focus:border-line-strong disabled:cursor-not-allowed disabled:opacity-60"
            />
            {streaming ? (
              <Button type="button" variant="outline" onClick={stop} title="Stop generating">
                <Square className="h-4 w-4" />
              </Button>
            ) : (
              <Button type="submit" disabled={!input.trim() || !model} title="Send">
                <Send className="h-4 w-4" />
              </Button>
            )}
          </form>
        </>
      )}
    </div>
  );
}

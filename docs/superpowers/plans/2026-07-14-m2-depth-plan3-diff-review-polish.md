# M2 Depth Plan 3 — Diff Review + Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two-`<pre>` staged-change review with real diff tabs in the editor pane, extract the staged queue to its own component, keep open editor tabs / diff tabs / tree badges in sync after apply/revert/SSE, fix the stale Pro-copy line, and add the release gates (external-URL bundle scan + bundle-size record + documented min-window check).

**Architecture:** Diff tabs reuse the existing `kind:"diff"` tab machinery. Their content (a fetched `StagedChangeDetail` + phase + stale flag) lives in the project-scoped `useEditorTabs` hook alongside file buffers; `EditorPane` dispatches on tab kind and renders a new read-only `StagedDiffView` (merge view) for diff tabs. The session-scoped staged-action handlers (apply/reject/revert/apply-all) stay in `chat.tsx` exactly as they are; Plan 3 routes the diff-tab footer and the (extracted) queue's "Review" through them, and wires the cross-store refresh (open file reload, diff-tab refresh, tree badge, SSE stale, session-switch close).

**Tech Stack:** React 18.3 + Vite 8 + Tailwind (existing), CodeMirror 6 `@codemirror/merge` (already a dep), vitest (already in-repo), Node (release-gate script). No new dependencies.

## Global Constraints

- Repo: `C:\Users\User\repos\model-hub`, branch `master` — **local-only, NEVER push (patent hold)**.
- Commits are auto-signed by repo-local config. Never `--no-verify`.
- Spec (do NOT copy its content into any file in this public repo): workspace repo `docs/superpowers/specs/2026-07-14-lac-m2-diff-editor-design.md`. Plan 3 implements spec §10 item 3; §5.2 (`diff-view.tsx` staged-review mode, `staged-queue.tsx`), §5.3 (state/data flow), §7 (release gates + copy ride-along) are the binding sections.
- **No external URLs / CDN anywhere in the editor stack** — this plan ADDS the gate that enforces it. Every CM6 import stays bundled ESM.
- The editor renders **disk truth only**; diff tabs render the staged snapshot (`old_content` → `new_content`) read-only. Neither ever loads the agent's live overlay.
- The session-scoped staged handlers and their identity guards (`isActiveSessionAction`, `changeBusyRef`, `sessionGenerationRef`) are shipped reality — do NOT change their guarding logic; only remove the `selectedChange` refetch blocks and add the cross-store refresh calls this plan specifies.
- Web gates in `web/`: `npm run typecheck` (bare — never pipe, it masks the exit code), `npm run build`, `npm run test` (vitest, currently 13 files / 107 tests green).
- Web tests run via vitest; `web/tests/**` is NOT type-checked by `npm run typecheck` (pre-existing tsconfig scope — out of scope here, logged backlog).
- Line anchors below were verified 2026-07-14 against working-tree master @ `27283a2`; re-check ±20 lines if a hunk doesn't match.
- Adjudicated scope decisions (carried for the final review — do NOT re-litigate mid-task):
  1. **Post-action disk sync is safe-only:** after apply/revert, an open FILE tab on the affected path reloads from disk ONLY if its buffer is clean (not dirty, not saving). A dirty buffer is left untouched and conflicts naturally at its own save (base-sha 409). No silent clobber.
  2. **SSE `staged_change` marks matching diff tabs stale** (banner + Refresh); it does not auto-refetch (the new event may be a different change id on the same path).
  3. **Diff tabs are closed on session switch** (they reference session-scoped changes); project switch already nukes them via the hook's `reset()`.
  4. **Min-window (1024×700) layout check stays a documented Duan-gated manual step** (packaged WebView2), like Ctrl+S — not automated. The external-URL scan and bundle-size record ARE automated.

---

### Task 1: `changeIdOfTabId` pure helper + test

**Files:**
- Modify: `web/src/lib/workbench-tabs.ts` (add one export beside `filePathOfTabId` `:65-67`)
- Modify: `web/src/lib/workbench-tabs.test.ts` (append)

**Interfaces:**
- Produces: `changeIdOfTabId(id: string): string | null` — returns the change id for a `"diff:"` tab id, else null. Consumed by Task 3 (hook `close`, EditorPane dispatch).

- [ ] **Step 1: Write the failing test** (append to `web/src/lib/workbench-tabs.test.ts`, inside the existing `describe("tab transitions", …)` block or a new `describe`)

```typescript
import { changeIdOfTabId } from "./workbench-tabs";

describe("changeIdOfTabId", () => {
  it("extracts the change id from a diff tab id; null for file ids", () => {
    expect(changeIdOfTabId("diff:abc123")).toBe("abc123");
    expect(changeIdOfTabId("file:src/a.ts")).toBeNull();
    expect(changeIdOfTabId("diff:")).toBe("");
  });
});
```

(Add `changeIdOfTabId` to the existing top-of-file import from `"./workbench-tabs"` rather than a second import statement if the file already imports from it.)

- [ ] **Step 2: Run to verify failure**

Run in `web/`: `npm run test`
Expected: FAIL — `changeIdOfTabId` is not exported.

- [ ] **Step 3: Implement** — in `web/src/lib/workbench-tabs.ts`, directly after `filePathOfTabId`:

```typescript
export function changeIdOfTabId(id: string): string | null {
  return id.startsWith("diff:") ? id.slice("diff:".length) : null;
}
```

- [ ] **Step 4: Run to verify pass + gates**

Run in `web/`: `npm run test` (all green) then `npm run typecheck` (exit 0).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/workbench-tabs.ts web/src/lib/workbench-tabs.test.ts
git commit -m "feat(web): changeIdOfTabId helper for diff tabs"
```

---

### Task 2: `StagedDiffView` — read-only staged-review merge view

**Files:**
- Modify: `web/src/components/workbench/diff-view.tsx` (add a second exported component beside `SaveConflictView`)

**Interfaces:**
- Consumes: `@codemirror/merge` `MergeView`, `./cm-theme` (both already used by `SaveConflictView`); `StagedChangeStatus` from `@/lib/types`.
- Produces: `StagedDiffView` with props `{ path: string; oldContent: string | null; newContent: string; status: StagedChangeStatus; stale: boolean; busy: boolean; onApply: () => void; onReject: () => void; onRevert: () => void; onRefresh: () => void }`. Consumed by Task 3's EditorPane.
- Both merge panes are **read-only** (staged review, not editing). New-file case: `oldContent` null → empty left pane.

- [ ] **Step 1: Implement** — append to `web/src/components/workbench/diff-view.tsx` (keep `SaveConflictView` unchanged; add the `StagedChangeStatus` type import at the top):

```tsx
import type { StagedChangeStatus } from "@/lib/types";

interface StagedDiffViewProps {
  path: string;
  oldContent: string | null;
  newContent: string;
  status: StagedChangeStatus;
  stale: boolean;
  busy: boolean;
  onApply: () => void;
  onReject: () => void;
  onRevert: () => void;
  onRefresh: () => void;
}

export function StagedDiffView({
  path,
  oldContent,
  newContent,
  status,
  stale,
  busy,
  onApply,
  onReject,
  onRevert,
  onRefresh,
}: StagedDiffViewProps) {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const readOnly = [
      lineNumbers(),
      lacEditorTheme,
      lacSyntaxHighlighting,
      EditorState.readOnly.of(true),
      EditorView.editable.of(false),
    ];
    const view = new MergeView({
      a: { doc: oldContent ?? "", extensions: readOnly },
      b: { doc: newContent, extensions: readOnly },
      parent: host,
    });
    return () => view.destroy();
  }, [oldContent, newContent, path]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <div className="min-w-0 truncate text-[12px] text-fg-muted">
          {oldContent === null ? "New file" : "Snapshot"} → proposed · <span className="uppercase tracking-[0.06em] text-fg-faint">{status}</span>
        </div>
        <div className="flex shrink-0 gap-2">
          {status === "pending" && (
            <>
              <Button size="sm" variant="danger" disabled={busy} onClick={onReject}>
                Reject
              </Button>
              <Button size="sm" disabled={busy} onClick={onApply}>
                Apply to disk
              </Button>
            </>
          )}
          {status === "applied" && (
            <Button size="sm" variant="ghost" disabled={busy} onClick={onRevert}>
              Revert
            </Button>
          )}
        </div>
      </div>
      {stale && (
        <div className="flex items-center justify-between gap-2 border-b border-warning/30 bg-warning-soft px-3 py-1.5 text-[11.5px] text-warning">
          <span>This file was re-staged. Refresh to see the latest.</span>
          <Button size="sm" variant="ghost" disabled={busy} onClick={onRefresh}>
            Refresh
          </Button>
        </div>
      )}
      <div ref={hostRef} className="min-h-0 flex-1 overflow-auto" />
    </div>
  );
}
```

- [ ] **Step 2: Gates**

Run in `web/`: `npm run typecheck` then `npm run build` (StagedDiffView is not imported yet — typecheck covers it; build won't bundle it). Both exit 0.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/workbench/diff-view.tsx
git commit -m "feat(web): read-only StagedDiffView merge view for staged review"
```

---

### Task 3: Editor hook diff-tab store + EditorPane diff rendering

**Files:**
- Modify: `web/src/components/workbench/use-editor-tabs.ts`
- Modify: `web/src/components/workbench/editor-pane.tsx`

**Interfaces:**
- Consumes: Task 1 `changeIdOfTabId`; Task 2 `StagedDiffView`; `api.stagedChange(changeId)` (`web/src/lib/api.ts:438`, returns `StagedChangeDetail`); `StagedChangeDetail`, `StagedChangeSummary` from `@/lib/types`.
- Produces (chat.tsx codes against these in Tasks 4-5):
  - hook adds `diffTabs: Map<string, DiffTabState>` and methods `openDiff(change: StagedChangeSummary)`, `refreshDiff(changeId: string)`, `markDiffStaleForPath(path: string)`, `closeDiffs()`, `syncDiskForPath(path: string)`.
  - `interface DiffTabState { phase: "loading" | "ready" | "error"; detail: import("@/lib/types").StagedChangeDetail | null; stale: boolean; error: { status: number | null; message: string } | null }`
  - EditorPane gains props `diffTabs: ReadonlyMap<string, DiffTabState>`, `onDiffApply: (changeId: string) => void`, `onDiffReject: (changeId: string) => void`, `onDiffRevert: (changeId: string) => void`, `onRefreshDiff: (changeId: string) => void`.

- [ ] **Step 1: Hook — add the diff-tab store** (`use-editor-tabs.ts`)

Add to the imports from `@/lib/workbench-tabs` (currently `:9-24`): `changeIdOfTabId`. Add near the `FileBuffer` interface (`:26`):

```typescript
export interface DiffTabState {
  phase: "loading" | "ready" | "error";
  detail: import("@/lib/types").StagedChangeDetail | null;
  stale: boolean;
  error: { status: number | null; message: string } | null;
}

function diffErrorMessage(status: number | null): string {
  if (status === 404) return "This staged change is no longer available.";
  if (status === 409) return "The project registration changed; re-pick the project.";
  return "This staged change could not be loaded.";
}
```

Add state beside `buffers`/`dirty` (`:85-87`):

```typescript
  const [diffTabs, setDiffTabs] = useState<Map<string, DiffTabState>>(new Map());
  const diffTabsRef = useRef(diffTabs);
  diffTabsRef.current = diffTabs;
```

Extend `reset` (`:105-110`) to also clear diff tabs:

```typescript
  const reset = useCallback(() => {
    sequenceRef.current += 1;
    setTabs(emptyTabs);
    setBuffers(new Map());
    setDirty(new Set());
    setDiffTabs(new Map());
  }, []);
```

Add a diff patch helper beside `patchBuffer` (`:95-103`):

```typescript
  const patchDiff = useCallback((changeId: string, patch: Partial<DiffTabState>) => {
    setDiffTabs((current) => {
      const existing = current.get(changeId);
      if (!existing) return current;
      const next = new Map(current);
      next.set(changeId, { ...existing, ...patch });
      return next;
    });
  }, []);
```

Extend `close` (`:187-211`) so closing a diff tab drops its store entry (the dirty-confirm block is unchanged — diff ids yield a null path so they skip it):

```typescript
  const close = useCallback((id: string) => {
    const path = filePathOfTabId(id);
    if (
      path &&
      dirtyRef.current.has(path) &&
      !window.confirm(`Discard unsaved changes to ${path}?`)
    ) {
      return;
    }
    setTabs((current) => closeTab(current, id));
    if (path) {
      setBuffers((current) => {
        if (!current.has(path)) return current;
        const next = new Map(current);
        next.delete(path);
        return next;
      });
      setDirty((current) => {
        if (!current.has(path)) return current;
        const next = new Set(current);
        next.delete(path);
        return next;
      });
    }
    const changeId = changeIdOfTabId(id);
    if (changeId) {
      setDiffTabs((current) => {
        if (!current.has(changeId)) return current;
        const next = new Map(current);
        next.delete(changeId);
        return next;
      });
    }
  }, []);
```

Add the diff-fetch loader + the five methods (place after `keepEditing`, before the `return`):

```typescript
  const loadDiff = useCallback(
    async (changeId: string) => {
      const sequence = sequenceRef.current;
      const pid = projectIdRef.current;
      try {
        const detail = await api.stagedChange(changeId);
        if (!isCurrent(sequence, pid)) return;
        setDiffTabs((current) => {
          if (!current.has(changeId)) return current;
          const next = new Map(current);
          next.set(changeId, { phase: "ready", detail, stale: false, error: null });
          return next;
        });
      } catch (error) {
        if (!isCurrent(sequence, pid)) return;
        const status = error instanceof ApiError ? error.status : null;
        patchDiff(changeId, {
          phase: "error",
          error: { status, message: diffErrorMessage(status) },
        });
      }
    },
    [isCurrent, patchDiff]
  );

  const openDiff = useCallback(
    (change: StagedChangeSummary) => {
      setTabs((current) => openTab(current, { kind: "diff", key: change.id }));
      if (diffTabsRef.current.has(change.id)) return;
      setDiffTabs((current) =>
        new Map(current).set(change.id, {
          phase: "loading",
          detail: null,
          stale: false,
          error: null,
        })
      );
      void loadDiff(change.id);
    },
    [loadDiff]
  );

  const refreshDiff = useCallback(
    (changeId: string) => {
      if (!diffTabsRef.current.has(changeId)) return;
      patchDiff(changeId, { stale: false });
      void loadDiff(changeId);
    },
    [loadDiff, patchDiff]
  );

  const markDiffStaleForPath = useCallback((path: string) => {
    setDiffTabs((current) => {
      let changed = false;
      const next = new Map(current);
      for (const [id, state] of current) {
        if (state.detail?.path === path && !state.stale) {
          next.set(id, { ...state, stale: true });
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, []);

  const closeDiffs = useCallback(() => {
    setTabs((current) => {
      const remaining = current.tabs.filter((tab) => tab.kind !== "diff");
      if (remaining.length === current.tabs.length) return current;
      const active =
        current.active && remaining.some((tab) => tabId(tab) === current.active)
          ? current.active
          : remaining.length
            ? tabId(remaining[remaining.length - 1])
            : null;
      return { tabs: remaining, active };
    });
    setDiffTabs(new Map());
  }, []);

  const syncDiskForPath = useCallback(
    (path: string) => {
      const buffer = buffersRef.current.get(path);
      if (!buffer || buffer.phase !== "ready") return;
      if (dirtyRef.current.has(path) || buffer.save.phase === "saving") return;
      void loadBuffer(path);
    },
    [loadBuffer]
  );
```

Add `tabId` to the `@/lib/workbench-tabs` import list (needed by `closeDiffs`). Extend the returned object (`:313-326`) with:

```typescript
    diffTabs,
    openDiff,
    refreshDiff,
    markDiffStaleForPath,
    closeDiffs,
    syncDiskForPath,
```

- [ ] **Step 2: EditorPane — dispatch on tab kind** (`editor-pane.tsx`)

Update the import (`:7`) to add `changeIdOfTabId` and `findTab`:

```tsx
import { type TabsState, changeIdOfTabId, filePathOfTabId, findTab, tabId } from "@/lib/workbench-tabs";
```

Add to the diff-view import (`:9`):

```tsx
import { SaveConflictView, StagedDiffView } from "./diff-view";
```

Add `DiffTabState` to the type import (`:10`):

```tsx
import type { DiffTabState, FileBuffer } from "./use-editor-tabs";
```

Extend `EditorPaneProps` (`:12-23`) with:

```tsx
  diffTabs: ReadonlyMap<string, DiffTabState>;
  onDiffApply: (changeId: string) => void;
  onDiffReject: (changeId: string) => void;
  onDiffRevert: (changeId: string) => void;
  onRefreshDiff: (changeId: string) => void;
```

Destructure them in the component signature. Replace the active-content computation (`:37-38`) and the render dispatch. New active computation:

```tsx
  const activeTab = tabs.active ? findTab(tabs, tabs.active) : undefined;
  const activePath = activeTab?.kind === "file" ? activeTab.key : null;
  const activeChangeId = activeTab?.kind === "diff" ? activeTab.key : null;
  const buffer = activePath ? buffers.get(activePath) : undefined;
  const diff = activeChangeId ? diffTabs.get(activeChangeId) : undefined;
```

In the tab-strip label line (`:51`), `tab.key` for a diff tab is a change id (opaque); render a friendlier label — replace the `label` const:

```tsx
            const label =
              tab.kind === "diff"
                ? (diffTabs.get(tab.key)?.detail?.path.split("/").pop() ?? "diff")
                : tab.key.slice(tab.key.lastIndexOf("/") + 1);
```

Replace the content-area conditional (`:94-143`, the `{!activePath || !buffer ? … }` chain) with a kind-aware version. Keep the file branches byte-identical; add the diff branch first:

```tsx
      <div className="min-h-0 flex-1">
        {activeChangeId && diff ? (
          diff.phase === "loading" ? (
            <div role="status" aria-live="polite" className="p-4 text-[12.5px] text-fg-muted">
              Loading change…
            </div>
          ) : diff.phase === "error" || !diff.detail ? (
            <div role="alert" className="m-4 rounded border border-warning/30 bg-warning-soft p-3 text-[12.5px] text-warning">
              {diff.error?.message ?? "This staged change could not be loaded."}
            </div>
          ) : (
            <StagedDiffView
              path={diff.detail.path}
              oldContent={diff.detail.old_content}
              newContent={diff.detail.new_content}
              status={diff.detail.status}
              stale={diff.stale}
              busy={false}
              onApply={() => onDiffApply(activeChangeId)}
              onReject={() => onDiffReject(activeChangeId)}
              onRevert={() => onDiffRevert(activeChangeId)}
              onRefresh={() => onRefreshDiff(activeChangeId)}
            />
          )
        ) : !activePath || !buffer ? (
          <div className="flex h-full items-center justify-center p-4">{emptyState}</div>
        ) : buffer.phase === "loading" ? (
          /* …EXISTING file branches unchanged from here… */
```

(Keep every existing file branch from `buffer.phase === "loading"` through the editor+Save footer exactly as-is; only the outer condition gained the diff branch ahead of it.)

- [ ] **Step 3: Gates**

Run in `web/`: `npm run typecheck`, `npm run build`, `npm run test` — all exit 0. (EditorPane's new props are required; chat.tsx doesn't pass them yet, so **typecheck will FAIL at the chat.tsx call site** — that is expected and is closed in Task 4. To keep THIS task green in isolation, make the five new EditorPane props optional with safe defaults is NOT allowed (they're load-bearing). Instead: this task's gate is `npm run test` + `npm run build` of the two changed files' own correctness via typecheck of those files. Accept a single chat.tsx typecheck error naming the missing props; Task 4 clears it. Record this expected-red in the report.)

- [ ] **Step 4: Commit**

```bash
git add web/src/components/workbench/use-editor-tabs.ts web/src/components/workbench/editor-pane.tsx
git commit -m "feat(web): diff-tab store in editor hook + EditorPane diff rendering"
```

---

### Task 4: staged-queue extraction + Review opens a diff tab + wire diff actions

**Files:**
- Create: `web/src/components/workbench/staged-queue.tsx`
- Modify: `web/src/pages/chat.tsx`

**Interfaces:**
- Consumes: Task 3 hook methods (`editor.openDiff`, `editor.diffTabs`, `editor.refreshDiff`) + EditorPane's new props; existing `applyChange`/`rejectChange`/`revertChange`/`applyAllChanges` handlers; `StagedChangeSummary`, `stagedFullPath`, `stagedStatusVariant`, `formatBytes` (already in chat.tsx).
- Produces: `StagedQueue` component (the extracted list, Apply-all + per-row Review/Apply/Reject/Revert — NO inline `<pre>` panel). Wired so "Review" opens a diff tab and diff-tab footer actions run the existing handlers.

- [ ] **Step 1: Create `web/src/components/workbench/staged-queue.tsx`**

Move the list portion of chat.tsx's `StagedChangesPanel` (`:1887-1955`, the header + rows) into a component; DROP the `selected`/`<pre>` block (`:1957-1974`). `stagedFullPath` is imported from `@/lib/agent-workbench`. `stagedStatusVariant` (chat.tsx `:2145`) and `formatBytes` (chat.tsx `:2155`) are used ONLY by the panel being deleted, so **MOVE them here** (define them in this file) and delete the chat.tsx originals in Step 2 — this is a move, not a duplication.

```tsx
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { stagedFullPath } from "@/lib/agent-workbench";
import type { StagedChangeSummary } from "@/lib/types";

function stagedStatusVariant(
  status: StagedChangeSummary["status"]
): "neutral" | "success" | "warning" | "danger" | "info" {
  if (status === "pending") return "warning";
  if (status === "applied") return "success";
  if (status === "conflict") return "danger";
  if (status === "reverted") return "info";
  return "neutral";
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function StagedQueue({
  changes,
  busy,
  onReview,
  onApply,
  onReject,
  onRevert,
  onApplyAll,
}: {
  changes: StagedChangeSummary[];
  busy: string;
  onReview: (change: StagedChangeSummary) => void;
  onApply: (change: StagedChangeSummary) => void;
  onReject: (change: StagedChangeSummary) => void;
  onRevert: (change: StagedChangeSummary) => void;
  onApplyAll: () => void;
}) {
  const pending = changes.filter((change) => change.status === "pending").length;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
          Staged changes
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={pending ? "warning" : "neutral"}>{pending} pending</Badge>
          {pending > 1 && (
            <Button size="sm" variant="ghost" onClick={onApplyAll}>
              Apply all
            </Button>
          )}
        </div>
      </div>
      <div className="space-y-2">
        {changes.slice().reverse().map((change) => {
          const isBusy = Boolean(busy);
          const fullPath = stagedFullPath(change.root, change.path);
          return (
            <div key={change.id} className="rounded border border-line bg-panel-2 p-2.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 break-all text-[12px] font-medium text-fg">{fullPath}</div>
                <Badge variant={stagedStatusVariant(change.status)}>{change.status}</Badge>
              </div>
              <div className="mt-1 break-all text-[11px] text-fg-faint">Run: {change.run_id}</div>
              <div className="mt-0.5 text-[11px] text-fg-faint">{formatBytes(change.new_size)} proposed</div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <Button size="sm" variant="ghost" disabled={isBusy} onClick={() => onReview(change)}>
                  Review
                </Button>
                {change.status === "pending" && (
                  <>
                    <Button size="sm" disabled={isBusy} onClick={() => onApply(change)}>
                      Apply to disk
                    </Button>
                    <Button size="sm" variant="danger" disabled={isBusy} onClick={() => onReject(change)}>
                      Reject
                    </Button>
                  </>
                )}
                {change.status === "applied" && (
                  <Button size="sm" variant="ghost" disabled={isBusy} onClick={() => onRevert(change)}>
                    Revert
                  </Button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: chat.tsx — remove the in-file `StagedChangesPanel` + `selectedChange`**

- Delete the whole in-file `StagedChangesPanel` function (`:1887-1976`), and the now-dead chat.tsx-local helpers `stagedStatusVariant` (`:2145`) and `formatBytes` (`:2155`) — they moved to `staged-queue.tsx` in Step 1 and have no other caller (grep `formatBytes`/`stagedStatusVariant` in chat.tsx after deleting the panel to confirm zero remaining uses before removing them).
- Delete the `selectedChange` state + its ref usages: `const [selectedChange, setSelectedChange] = useState<StagedChangeDetail | null>(null);` (`:249`) and every `setSelectedChange(...)` / `selectedChange` read (in `reviewStagedChange`, `applyChange`, `rejectChange`, `revertChange`, `applyAllChanges`, and `clearStagedContext`/`refreshStagedChanges` if referenced). Grep `selectedChange` and remove each site:
  - `reviewStagedChange` (`:1004-1033`): replace its whole body with the diff-tab open (see Step 3).
  - In `applyChange`/`rejectChange`/`revertChange` (`:1035+`): remove the trailing `const detail = await api.stagedChange(change.id); if (…) setSelectedChange(detail);` block after `await refreshStagedChanges(change.session_id);` — Task 5 adds the diff-refresh instead; for THIS task, just delete those refetch blocks.
  - In `applyAllChanges` (`:1178+`): remove the `const selectedId = selectedChange?.id; …` refetch block.
  - In `refreshStagedChanges` (`:365+`): if it does `setSelectedChange((current) => …)`, remove that line (the queue list still updates via `setStagedChanges`).
  - Remove the now-unused `StagedChangeDetail` import if nothing else uses it (grep first).
- Add the import: `import { StagedQueue } from "@/components/workbench/staged-queue";`

- [ ] **Step 3: chat.tsx — `reviewStagedChange` opens a diff tab**

Replace the entire `reviewStagedChange` (`:1004-1033`) with:

```tsx
  const reviewStagedChange = (change: StagedChangeSummary) => {
    const identity = { sessionId: change.session_id, generation: sessionGenerationRef.current };
    if (!isActiveSessionAction(identity)) return;
    editor.openDiff(change);
    setMobilePane("editor");
  };
```

- [ ] **Step 4: chat.tsx — diff-tab action wrappers + EditorPane props**

Add these handlers beside `applyAllChanges` (resolve the change id to a summary from the live queue, run the existing session-guarded handler, then refresh the diff tab):

```tsx
  const changeById = (changeId: string) => stagedChanges.find((c) => c.id === changeId);
  const onDiffApply = async (changeId: string) => {
    const change = changeById(changeId);
    if (!change) return;
    await applyChange(change);
    editor.refreshDiff(changeId);
  };
  const onDiffReject = async (changeId: string) => {
    const change = changeById(changeId);
    if (!change) return;
    await rejectChange(change);
    editor.refreshDiff(changeId);
  };
  const onDiffRevert = async (changeId: string) => {
    const change = changeById(changeId);
    if (!change) return;
    await revertChange(change);
    editor.refreshDiff(changeId);
  };
```

Extend the `<EditorPane … />` invocation (`:1446-1473`) with the five new props:

```tsx
              diffTabs={editor.diffTabs}
              onDiffApply={(id) => void onDiffApply(id)}
              onDiffReject={(id) => void onDiffReject(id)}
              onDiffRevert={(id) => void onDiffRevert(id)}
              onRefreshDiff={(id) => editor.refreshDiff(id)}
```

- [ ] **Step 5: chat.tsx — swap the queue render**

In the left-rail staged section (`:1419-1431`), replace `<StagedChangesPanel … selected={selectedChange} …/>` with `<StagedQueue …/>` (drop the `selected` prop; keep `changes`/`busy`/`onReview`/`onApply`/`onReject`/`onRevert`/`onApplyAll`):

```tsx
                  <StagedQueue
                    changes={stagedChanges}
                    busy={changeBusy}
                    onReview={(change) => reviewStagedChange(change)}
                    onApply={(change) => void applyChange(change)}
                    onReject={(change) => void rejectChange(change)}
                    onRevert={(change) => void revertChange(change)}
                    onApplyAll={() => void applyAllChanges()}
                  />
```

Also update the section's mount condition (`:1404`) — it currently reads `(stagedChanges.length > 0 || selectedChange)`; drop the `selectedChange` term → `stagedChanges.length > 0`.

- [ ] **Step 6: Gates**

Run in `web/`: `npm run typecheck`, `npm run build`, `npm run test` — all exit 0 (Task 3's expected chat.tsx typecheck error is now resolved).

- [ ] **Step 7: Commit**

```bash
git add web/src/components/workbench/staged-queue.tsx web/src/pages/chat.tsx
git commit -m "feat(web): extract staged queue; Review opens a diff tab; wire diff actions"
```

---

### Task 5: Cross-store refresh — disk sync, SSE stale, session-switch close

**Files:**
- Modify: `web/src/pages/chat.tsx`

**Interfaces:**
- Consumes: Task 3 `editor.syncDiskForPath`, `editor.markDiffStaleForPath`, `editor.closeDiffs`.
- Produces: applied/reverted changes reload clean open file tabs; SSE `staged_change` marks matching diff tabs stale; session switches close all diff tabs.

- [ ] **Step 1: Disk sync after apply/revert**

In `applyChange` success path — after `await refreshStagedChanges(change.session_id);` (`:1056`) add:

```tsx
      editor.syncDiskForPath(change.path);
```

Same one line in `revertChange` after its `await refreshStagedChanges(change.session_id);`. (Reject does not change disk — no sync.) For `applyAllChanges`, after its `await refreshStagedChanges(sid);` (`:1199`), sync every applied path:

```tsx
      for (const c of stagedChanges) {
        if (c.status === "pending") editor.syncDiskForPath(c.path);
      }
```

(The pre-apply-all `stagedChanges` still lists the just-applied rows as their old status; syncing every path that was pending is correct — an unchanged path's clean buffer simply re-reads identical bytes.)

- [ ] **Step 2: SSE marks diff tabs stale**

In the SSE `staged_change` branch (`:848-856`), after `void refreshStagedChanges(streamSessionId, true);` add:

```tsx
          if (typeof ev.path === "string") editor.markDiffStaleForPath(ev.path);
```

- [ ] **Step 3: Close diff tabs on session switch**

`clearStagedContext` (grep it — it bumps `sessionGenerationRef` and clears `stagedChanges`) is the single choke point for session-context teardown. Add `editor.closeDiffs();` inside it. (Project/workspace switches call `resetWorkbenchContext` → `clearStagedContext`, and the editor hook's own project-`reset` also clears diff tabs, so this one call covers session switch, new session, and clear.)

- [ ] **Step 4: Gates**

Run in `web/`: `npm run typecheck`, `npm run build`, `npm run test` — all exit 0.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/chat.tsx
git commit -m "feat(web): sync open buffers + diff tabs after staged actions and SSE"
```

---

### Task 6: Copy ride-along + release gates

**Files:**
- Modify: `web/src/components/pro/product-spine.tsx` (`:75`)
- Create: `web/scripts/check-bundle.mjs`
- Modify: `web/package.json` (add a `check:bundle` script)
- Modify: `.github/workflows/build.yml` and `.github/workflows/test.yml` (add a `check:bundle` step after `npm run build` in the `web` job)

**Interfaces:**
- Produces: honest Pro copy (Build Mode is free); a post-build gate that fails on any CDN host in `dist/assets/*.js` and prints + ceiling-checks the editor-pane and index chunk sizes.

- [ ] **Step 1: Copy ride-along** — in `product-spine.tsx`, the "Build workbench" entry `pro` field (`:75`) currently reads `"Coding cockpit for agent readiness now; approval-gated Build mode is the next paid lane."` Replace with the free-Build framing (Duan's 2026-07-09 monetization call — Build Mode is free; Pro = build-readiness):

```tsx
    pro: "Build Mode is free on your local models; Pro makes them build-ready — per-model tuning and a readiness benchmark.",
```

Leave `licensedState`/`lockedState` as-is unless a test asserts the old string (grep `next paid lane` across `web/` and `tests/` first; if a test asserts it, update that assertion to match the new copy — do NOT weaken it, just retarget the substring).

- [ ] **Step 2: Create `web/scripts/check-bundle.mjs`**

```javascript
// Release gate: no CDN/external host in the shipped bundle, and the lazy
// editor chunk stays within a sane ceiling. Run AFTER `npm run build`.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const ASSETS = "dist/assets";
const CDN_HOSTS = [
  "cdn.jsdelivr.net",
  "unpkg.com",
  "cdnjs.cloudflare.com",
  "esm.sh",
  "jspm.dev",
  "cdn.skypack.dev",
  "ga.jspm.io",
];
// Raw-byte ceilings (not gzip). Editor chunk is CM6-heavy but must not balloon;
// index must NOT absorb CM6 (that would mean the lazy split broke).
const CEILINGS = { editorPane: 900_000, index: 700_000 };

let files;
try {
  files = readdirSync(ASSETS).filter((f) => f.endsWith(".js"));
} catch {
  console.error(`check-bundle: ${ASSETS} not found — run \`npm run build\` first.`);
  process.exit(1);
}

const failures = [];
let editorBytes = 0;
let indexBytes = 0;

for (const file of files) {
  const path = join(ASSETS, file);
  const text = readFileSync(path, "utf8");
  for (const host of CDN_HOSTS) {
    if (text.includes(host)) failures.push(`CDN host "${host}" found in ${file}`);
  }
  const bytes = statSync(path).size;
  if (file.startsWith("editor-pane")) editorBytes = bytes;
  if (file.startsWith("index")) indexBytes = bytes;
}

console.log(`check-bundle: editor-pane=${editorBytes}B index=${indexBytes}B`);
if (editorBytes === 0) failures.push("editor-pane chunk missing (lazy split broken?)");
if (editorBytes > CEILINGS.editorPane) failures.push(`editor-pane ${editorBytes}B > ceiling ${CEILINGS.editorPane}B`);
if (indexBytes > CEILINGS.index) failures.push(`index ${indexBytes}B > ceiling ${CEILINGS.index}B (CM6 may have leaked into the initial chunk)`);

if (failures.length) {
  console.error("check-bundle FAILED:\n" + failures.map((f) => `  - ${f}`).join("\n"));
  process.exit(1);
}
console.log("check-bundle OK");
```

- [ ] **Step 3: `web/package.json`** — add to `scripts`:

```json
    "check:bundle": "node scripts/check-bundle.mjs",
```

- [ ] **Step 4: CI wiring** — in BOTH `.github/workflows/build.yml` (the `web` working-directory block, after `npm run build`) and `.github/workflows/test.yml` (same), add a line so the gate runs post-build:

```yaml
          npm run check:bundle
```

Insert it immediately after the existing `npm run build` line in each `web` job's run block (keep `npm audit` after it if present).

- [ ] **Step 5: Verify the gate locally**

Run in `web/`: `npm run build` then `npm run check:bundle` — expect `check-bundle OK` and printed sizes (editor-pane ~600kB, index ~520kB, both under ceiling). Also run `npm run typecheck` (product-spine change) exit 0.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/pro/product-spine.tsx web/scripts/check-bundle.mjs web/package.json .github/workflows/build.yml .github/workflows/test.yml
git commit -m "feat(web): free-Build Pro copy + post-build bundle gate (no CDN, size ceiling)"
```

---

### Task 7: Full verification + ledger (controller-run)

- [ ] **Step 1: Full non-live pytest**

Run: `.venv\Scripts\python.exe -m pytest -m "not live" -q`
Expected: green except the ONE known pre-existing red on master: `tests/test_release_readiness.py::test_build_workflow_verifies_source_version_and_uploads_checksum` (NOT ours — do not fix, do not mask). Env skips (symlink/Ollama) expected. (Backend is untouched this plan — this is a regression check only.)

- [ ] **Step 2: Web gates**

Run in `web/`: `npm run typecheck` && `npm run build` && `npm run check:bundle` && `npm run test` — all exit 0 / OK.

- [ ] **Step 3: Live editor smoke (controller, real isolated server)**

Register a project; stage an agent change (or reuse Plan 2's HTTP smoke to stage one); then:
1. Queue "Review" → a diff tab opens in the editor pane (old→new merge, read-only), status pending, Apply/Reject in the footer.
2. Apply from the diff footer → toast applied; queue row → applied; diff tab refetches → status applied, footer now shows Revert; if that file is open + clean in an editor tab, it reloads to the applied content.
3. Revert from the diff tab → disk restored; open clean buffer reloads.
4. Re-stage the same path (agent) → open diff tab shows the stale banner + Refresh; Refresh reloads its detail.
5. Switch session → diff tabs close; file tabs (project-scoped) survive.
6. `check:bundle` prints editor-pane + index sizes; grep the built `dist/assets/*.js` shows no CDN host.

- [ ] **Step 4: Ledger**

Append a `# M2 depth plan 3 - diff review + polish (2026-07-14)` section to `.superpowers/sdd/progress.md` (per-task commits, test counts, review verdicts, carried Minors). `.superpowers/` is gitignored — do NOT commit it.

---

## Self-review (spec §10.3 coverage)

- Diff tabs replacing the `<pre>` panel → Tasks 2, 3, 4. Staged-queue extraction w/ Apply-all/Revert → Task 4. Post-apply refresh (open buffers + diff tabs + tree badges via existing `refreshStagedChanges` → `pendingStagedPaths`) → Tasks 4, 5. SSE integration → Task 5. Copy ride-along → Task 6. Release gates (external-URL scan + bundle record) → Task 6; min-window check → documented Duan-gated (Global Constraint 4).
- Spec §5.3 data flow: SSE refresh queue + stale open diff tab + tree badge (badge already live via `pendingStagedPaths`); post-apply refetch open tabs + refresh tree dir (badge derives from `stagedChanges`, refreshed by the existing handlers) + mark diff tab (refreshDiff) — all covered.
- Deliberately NOT here: hunk-level approve/reject, editing the agent's proposed content in-diff, autosave (all YAGNI-deferred in the spec §1 non-goals).

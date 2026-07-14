// Center column of the workbench: tab strip + active buffer.
// Loaded via React.lazy so CodeMirror stays out of /chat's initial chunk.
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { type TabsState, filePathOfTabId, tabId } from "@/lib/workbench-tabs";
import { CodeEditor } from "./code-editor";
import { SaveConflictView } from "./diff-view";
import type { FileBuffer } from "./use-editor-tabs";

interface EditorPaneProps {
  tabs: TabsState;
  buffers: ReadonlyMap<string, FileBuffer>;
  dirty: ReadonlySet<string>;
  emptyState: ReactNode;
  onActivate: (id: string) => void;
  onClose: (id: string) => void;
  onChangeDoc: (path: string, doc: string) => void;
  onSave: (path: string) => void;
  onSaveAgain: (path: string, content: string) => void;
  onKeepEditing: (path: string, content: string) => void;
}

export default function EditorPane({
  tabs,
  buffers,
  dirty,
  emptyState,
  onActivate,
  onClose,
  onChangeDoc,
  onSave,
  onSaveAgain,
  onKeepEditing,
}: EditorPaneProps) {
  const activePath = tabs.active ? filePathOfTabId(tabs.active) : null;
  const buffer = activePath ? buffers.get(activePath) : undefined;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {tabs.tabs.length > 0 && (
        <div
          role="tablist"
          aria-label="Open editor tabs"
          className="flex items-center gap-0.5 overflow-x-auto border-b border-line px-1.5 py-1"
        >
          {tabs.tabs.map((tab) => {
            const id = tabId(tab);
            const active = tabs.active === id;
            const label = tab.key.slice(tab.key.lastIndexOf("/") + 1);
            const isDirty = tab.kind === "file" && dirty.has(tab.key);
            return (
              <div
                key={id}
                className={cn(
                  "flex shrink-0 items-center rounded",
                  active ? "bg-panel-3" : "hover:bg-panel-2"
                )}
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={active}
                  title={tab.key}
                  className={cn(
                    "max-w-[180px] truncate px-2 py-1 text-[12px]",
                    active ? "text-fg" : "text-fg-muted"
                  )}
                  onClick={() => onActivate(id)}
                >
                  {isDirty && (
                    <span
                      aria-label="Unsaved changes"
                      className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-warning align-middle"
                    />
                  )}
                  {label}
                </button>
                <button
                  type="button"
                  aria-label={`Close ${label}`}
                  className="rounded p-0.5 text-fg-faint hover:text-fg"
                  onClick={() => onClose(id)}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            );
          })}
        </div>
      )}

      <div className="min-h-0 flex-1">
        {!activePath || !buffer ? (
          <div className="flex h-full items-center justify-center p-4">{emptyState}</div>
        ) : buffer.phase === "loading" ? (
          <div role="status" aria-live="polite" className="p-4 text-[12.5px] text-fg-muted">
            Loading {activePath}…
          </div>
        ) : buffer.phase === "error" ? (
          <div
            role="alert"
            className="m-4 rounded border border-warning/30 bg-warning-soft p-3 text-[12.5px] text-warning"
          >
            {buffer.error?.message ?? "This file could not be opened."}
          </div>
        ) : buffer.conflict ? (
          <SaveConflictView
            path={activePath}
            diskContent={buffer.conflict.diskContent}
            bufferContent={buffer.doc}
            busy={buffer.save.phase === "saving"}
            onSaveAgain={(content) => onSaveAgain(activePath, content)}
            onKeepEditing={(content) => onKeepEditing(activePath, content)}
          />
        ) : (
          <div className="flex h-full min-h-0 flex-col">
            <div className="min-h-0 flex-1">
              <CodeEditor
                path={activePath}
                doc={buffer.doc}
                docVersion={buffer.docVersion}
                onChange={(doc) => onChangeDoc(activePath, doc)}
                onSave={() => onSave(activePath)}
              />
            </div>
            <div className="flex items-center justify-between gap-2 border-t border-line px-3 py-1.5">
              <div className="min-w-0 truncate text-[11px] text-fg-faint">
                {activePath}
                {buffer.save.phase === "missing" && " — gone from disk; Save recreates it"}
              </div>
              <Button
                size="sm"
                disabled={buffer.save.phase === "saving" || !dirty.has(activePath)}
                onClick={() => onSave(activePath)}
              >
                {buffer.save.phase === "saving" ? "Saving…" : "Save"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

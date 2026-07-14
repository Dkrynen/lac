// Merge view for the save-conflict flow: disk truth (left, read-only) vs the
// user's buffer (right, editable). Staged-review mode arrives in plan 3.
import { useEffect, useRef } from "react";
import { MergeView } from "@codemirror/merge";
import { EditorState } from "@codemirror/state";
import { EditorView, lineNumbers } from "@codemirror/view";
import { Button } from "@/components/ui/button";
import { lacEditorTheme, lacSyntaxHighlighting } from "./cm-theme";
import type { StagedChangeStatus } from "@/lib/types";

interface SaveConflictViewProps {
  path: string;
  diskContent: string;
  bufferContent: string;
  busy: boolean;
  onSaveAgain: (editedContent: string) => void;
  onKeepEditing: (editedContent: string) => void;
}

export function SaveConflictView({
  path,
  diskContent,
  bufferContent,
  busy,
  onSaveAgain,
  onKeepEditing,
}: SaveConflictViewProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mergeRef = useRef<MergeView | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const shared = [lineNumbers(), lacEditorTheme, lacSyntaxHighlighting];
    const view = new MergeView({
      a: {
        doc: diskContent,
        extensions: [...shared, EditorState.readOnly.of(true), EditorView.editable.of(false)],
      },
      b: { doc: bufferContent, extensions: shared },
      parent: host,
    });
    mergeRef.current = view;
    return () => {
      mergeRef.current = null;
      view.destroy();
    };
  }, [diskContent, bufferContent, path]);

  const currentRight = () => mergeRef.current?.b.state.doc.toString() ?? bufferContent;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <div className="min-w-0 truncate text-[12px] text-warning">
          Disk changed since this file was loaded — left is disk, right is your edit.
        </div>
        <div className="flex shrink-0 gap-2">
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onKeepEditing(currentRight())}>
            Keep editing
          </Button>
          <Button size="sm" disabled={busy} onClick={() => onSaveAgain(currentRight())}>
            {busy ? "Saving…" : "Save again"}
          </Button>
        </div>
      </div>
      <div ref={hostRef} className="min-h-0 flex-1 overflow-auto" />
    </div>
  );
}

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

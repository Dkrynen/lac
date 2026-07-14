// Merge view for the save-conflict flow: disk truth (left, read-only) vs the
// user's buffer (right, editable). Staged-review mode arrives in plan 3.
import { useEffect, useRef } from "react";
import { MergeView } from "@codemirror/merge";
import { EditorState } from "@codemirror/state";
import { EditorView, lineNumbers } from "@codemirror/view";
import { Button } from "@/components/ui/button";
import { lacEditorTheme, lacSyntaxHighlighting } from "./cm-theme";

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

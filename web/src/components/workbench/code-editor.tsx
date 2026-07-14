// CodeMirror 6 wrapper: uncontrolled view, controlled identity.
// The view is (re)created when the tab identity (path) or an external
// reload (docVersion) changes; keystrokes flow OUT through onChange.
import { useEffect, useRef } from "react";
import { EditorState, type Extension } from "@codemirror/state";
import {
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { bracketMatching, indentOnInput } from "@codemirror/language";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { yaml } from "@codemirror/lang-yaml";
import { languageIdForPath } from "@/lib/workbench-tabs";
import { lacEditorTheme, lacSyntaxHighlighting } from "./cm-theme";

function languageExtension(path: string): Extension {
  const choice = languageIdForPath(path);
  if (!choice) return [];
  switch (choice.id) {
    case "python":
      return python();
    case "javascript":
      return javascript({ typescript: choice.typescript, jsx: choice.jsx });
    case "json":
      return json();
    case "markdown":
      return markdown();
    case "html":
      return html();
    case "css":
      return css();
    case "yaml":
      return yaml();
  }
}

interface CodeEditorProps {
  path: string;
  doc: string;
  /** bump to force the view to reload `doc` from outside (create, save-again) */
  docVersion: number;
  onChange: (doc: string) => void;
  onSave: () => void;
}

export function CodeEditor({ path, doc, docVersion, onChange, onSave }: CodeEditorProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const onChangeRef = useRef(onChange);
  const onSaveRef = useRef(onSave);
  onChangeRef.current = onChange;
  onSaveRef.current = onSave;
  const docRef = useRef(doc);
  docRef.current = doc;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const view = new EditorView({
      state: EditorState.create({
        doc: docRef.current,
        extensions: [
          lineNumbers(),
          highlightActiveLineGutter(),
          highlightSpecialChars(),
          highlightActiveLine(),
          history(),
          bracketMatching(),
          indentOnInput(),
          languageExtension(path),
          lacEditorTheme,
          lacSyntaxHighlighting,
          keymap.of([
            // WebView2 swallows Ctrl+S into the host page otherwise;
            // preventDefault is load-bearing — verify in the packaged app.
            { key: "Mod-s", preventDefault: true, run: () => { onSaveRef.current(); return true; } },
            ...defaultKeymap,
            ...historyKeymap,
          ]),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) onChangeRef.current(update.state.doc.toString());
          }),
        ],
      }),
      parent: host,
    });
    return () => view.destroy();
    // doc intentionally NOT a dependency: content changes flow out via
    // onChange; docVersion is the explicit external-reload signal.
  }, [path, docVersion]);

  return <div ref={hostRef} className="h-full min-h-0 overflow-hidden" />;
}

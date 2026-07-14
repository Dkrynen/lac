// Undergrowth editor theme bound to the existing CSS design tokens
// (src/index.css) so light/dark palette flips apply automatically.
import { EditorView } from "@codemirror/view";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";

export const lacEditorTheme = EditorView.theme(
  {
    "&": {
      backgroundColor: "var(--surface)",
      color: "var(--text)",
      fontSize: "12.5px",
      height: "100%",
    },
    ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.55" },
    ".cm-content": { caretColor: "var(--accent)" },
    ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
      { backgroundColor: "var(--accent-soft)" },
    ".cm-gutters": {
      backgroundColor: "var(--surface)",
      color: "var(--text-faint)",
      border: "none",
      borderRight: "1px solid var(--border)",
    },
    ".cm-activeLine": { backgroundColor: "rgba(228, 232, 226, 0.04)" },
    ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--text-muted)" },
    ".cm-matchingBracket": { backgroundColor: "var(--accent-soft)", outline: "none" },
    "&.cm-focused": { outline: "none" },
  },
  { dark: true }
);

export const lacHighlightStyle = HighlightStyle.define([
  { tag: [t.keyword, t.moduleKeyword, t.controlKeyword, t.operatorKeyword], color: "var(--accent)" },
  { tag: [t.string, t.special(t.string), t.regexp], color: "var(--success)" },
  { tag: [t.number, t.bool, t.null, t.atom], color: "var(--info)" },
  { tag: [t.comment], color: "var(--text-faint)", fontStyle: "italic" },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: "var(--accent-hover)" },
  { tag: [t.typeName, t.className, t.namespace, t.tagName], color: "var(--warning)" },
  { tag: [t.propertyName, t.attributeName], color: "var(--text)" },
  { tag: [t.operator, t.punctuation, t.bracket], color: "var(--text-muted)" },
  { tag: t.heading, color: "var(--text)", fontWeight: "600" },
  { tag: [t.link, t.url], color: "var(--info)", textDecoration: "underline" },
  { tag: t.invalid, color: "var(--danger)" },
]);

export const lacSyntaxHighlighting = syntaxHighlighting(lacHighlightStyle);

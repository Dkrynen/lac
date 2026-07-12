import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const chatSource = readFileSync(new URL("../src/pages/chat.tsx", import.meta.url), "utf8");
const panelSource = readFileSync(
  new URL("../src/components/workbench/project-files-panel.tsx", import.meta.url),
  "utf8"
);

test("Workbench exposes a project-bound read-only Files navigator", () => {
  assert.match(chatSource, /<ProjectFilesPanel/);
  assert.match(chatSource, /key=\{selectedProjectId\}/);
  assert.match(chatSource, /projectId=\{selectedProjectId\}/);
  assert.match(chatSource, />\s*Files\s*</);
  assert.match(panelSource, /Read only/);
  assert.match(panelSource, /api\.projectFiles\(targetProjectId/);
  assert.match(panelSource, /api\.projectFile\(projectId/);
});

test("Project Files never receives or stores a raw project root", () => {
  assert.doesNotMatch(panelSource, /localStorage/);
  assert.doesNotMatch(panelSource, /sessionStorage/);
  assert.doesNotMatch(panelSource, /indexedDB/);
  assert.doesNotMatch(panelSource, /\bcwd\b/);
  assert.doesNotMatch(panelSource, /\bprojectRoot\b/);
  assert.doesNotMatch(panelSource, /\broot\s*:/);
  assert.match(panelSource, /isCurrentProjectFileRequest/);
});

test("Project Files aborts stale reads and renders project text inertly", () => {
  assert.match(panelSource, /AbortController/);
  assert.match(panelSource, /controller\.abort\(\)/);
  assert.match(panelSource, /<pre/);
  assert.match(panelSource, /<code>\{preview\.content\}<\/code>/);
  assert.doesNotMatch(panelSource, /dangerouslySetInnerHTML/);
  assert.doesNotMatch(panelSource, /<Markdown/);
});

test("Project Files exposes bounded and announced navigator state", () => {
  assert.match(chatSource, /h-\[520px\]/);
  assert.match(chatSource, /aria-pressed=\{navigatorView === "files"\}/);
  assert.match(panelSource, /aria-busy=/);
  assert.match(panelSource, /aria-live="polite"/);
  assert.match(panelSource, /isProjectIdentityDrift/);
});

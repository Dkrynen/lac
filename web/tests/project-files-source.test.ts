import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";

const chatSource = readFileSync(new URL("../src/pages/chat.tsx", import.meta.url), "utf8");
const treeSource = readFileSync(
  new URL("../src/components/workbench/file-tree.tsx", import.meta.url),
  "utf8"
);

test("Workbench exposes a project-bound Files tree", () => {
  assert.match(chatSource, /<FileTree/);
  assert.match(chatSource, /key=\{selectedProjectId\}/);
  assert.match(chatSource, /projectId=\{selectedProjectId\}/);
  assert.match(chatSource, /pendingPaths=\{pendingStagedPaths\}/);
  assert.match(chatSource, /onOpenFile=\{openFileInEditor\}/);
  assert.match(treeSource, /api\.projectFiles\(pid/);
});

test("File tree never receives or stores a raw project root", () => {
  assert.doesNotMatch(treeSource, /localStorage/);
  assert.doesNotMatch(treeSource, /sessionStorage/);
  assert.doesNotMatch(treeSource, /indexedDB/);
  assert.doesNotMatch(treeSource, /\bcwd\b/);
  assert.doesNotMatch(treeSource, /\bprojectRoot\b/);
  assert.doesNotMatch(treeSource, /\broot\s*:/);
});

test("File tree ignores stale directory reads and renders text inertly", () => {
  assert.match(treeSource, /sequenceRef\.current !== sequence/);
  assert.match(treeSource, /projectIdRef\.current !== pid/);
  assert.doesNotMatch(treeSource, /dangerouslySetInnerHTML/);
  assert.doesNotMatch(treeSource, /<Markdown/);
});

test("File tree exposes bounded and announced navigator state", () => {
  assert.match(chatSource, /h-\[520px\]/);
  assert.match(chatSource, /aria-pressed=\{navigatorView === "files"\}/);
  assert.match(treeSource, /role="alert"/);
  assert.match(treeSource, /role="status"/);
  assert.match(treeSource, /aria-expanded=\{open\}/);
});

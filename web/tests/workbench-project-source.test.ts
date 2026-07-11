import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/pages/chat.tsx", import.meta.url), "utf8");
const pickerSource = readFileSync(
  new URL("../src/components/workbench/context-picker.tsx", import.meta.url),
  "utf8"
);

test("Workbench source has no browser-stored root or global workspace mutation", () => {
  assert.doesNotMatch(source, /lac\.workbench\.projectRoot/);
  assert.doesNotMatch(source, /localStorage/);
  assert.doesNotMatch(source, /api\.switchWorkspace/);
  assert.doesNotMatch(source, /\bcwd\s*:/);
  assert.match(source, /<ContextPicker/);
  assert.match(source, />\s*Threads\s*</);
  assert.match(source, /const selectedProjectId = selectedProject\?\.id \?\? ""/);
});

test("registration description is a sanitized single-line field", () => {
  assert.doesNotMatch(pickerSource, /<textarea/);
  assert.match(pickerSource, /sanitizeProjectDescription\(event\.target\.value\)/);
});

test("project context reset clears thread-owned prompts and drafts", () => {
  const start = source.indexOf("const resetWorkbenchContext");
  const end = source.indexOf("const switchWorkspace", start);
  assert.ok(start >= 0 && end > start);
  const resetSource = source.slice(start, end);
  assert.match(resetSource, /setSystem\(""\)/);
  assert.match(resetSource, /setInput\(""\)/);
  assert.match(resetSource, /registrationRequestSequenceRef\.current \+= 1/);
  assert.match(resetSource, /registeringProjectRef\.current = false/);
  assert.match(resetSource, /setRegisteringProject\(false\)/);
});

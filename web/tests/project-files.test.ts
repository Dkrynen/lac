import assert from "node:assert/strict";
import { test } from "vitest";

import {
  decodeProjectFileDetail,
  decodeProjectFilesResponse,
  isCurrentProjectFileRequest,
  normalizeProjectFilePath,
  projectFileBreadcrumbs,
  projectFileChildPath,
  projectFileParentPath,
} from "../src/lib/project-files.ts";

test("project file navigation stays in portable relative paths", () => {
  assert.equal(projectFileChildPath("", "src"), "src");
  assert.equal(projectFileChildPath("src", "index.ts"), "src/index.ts");
  assert.equal(projectFileParentPath("src/lib/index.ts"), "src/lib");
  assert.equal(projectFileParentPath("src"), "");
  assert.deepEqual(projectFileBreadcrumbs("src/lib"), [
    { label: "Project", path: "" },
    { label: "src", path: "src" },
    { label: "lib", path: "src/lib" },
  ]);
  assert.throws(() => projectFileChildPath("src", "../secret"), /invalid project file name/i);
});

test("project file responses commit only to the exact current project request", () => {
  const request = { projectId: "project-a", sequence: 4 };
  assert.equal(isCurrentProjectFileRequest("project-a", 4, request), true);
  assert.equal(isCurrentProjectFileRequest("project-b", 4, request), false);
  assert.equal(isCurrentProjectFileRequest("project-a", 5, request), false);
});

test("project file paths fail closed on traversal, platform-specific and reserved names", () => {
  for (const path of [
    "/absolute",
    "../secret",
    "src\\secret",
    "src//file",
    "C:/secret",
    "src/file?.ts",
    "src/safe\u202etxt.js",
    "src/CON",
    "src/COM¹.log",
    "src/file. ",
  ]) {
    assert.throws(() => normalizeProjectFilePath(path), /invalid project file/i);
  }
});

test("project file decoders accept only exact bounded response contracts", () => {
  const listing = {
    path: "src",
    entries: [{ name: "index.ts", type: "file", size: 11 }],
    truncated: false,
  };
  assert.deepEqual(decodeProjectFilesResponse(listing, "src"), listing);
  assert.throws(
    () => decodeProjectFilesResponse({ ...listing, root: "C:/private" }, "src"),
    /invalid project files response/i
  );
  assert.throws(
    () => decodeProjectFilesResponse({ ...listing, entries: [{ name: "../secret", type: "file", size: 1 }] }),
    /invalid project files response/i
  );

  const content = "<script>alert(1)</script> [x](javascript:alert(1))";
  const detail = {
    path: "src/index.ts",
    content,
    sha256: "a".repeat(64),
    size: new TextEncoder().encode(content).byteLength,
  };
  assert.deepEqual(decodeProjectFileDetail(detail, "src/index.ts"), detail);
  assert.throws(
    () => decodeProjectFileDetail({ ...detail, sha256: "not-a-hash" }),
    /invalid project files response/i
  );
  assert.throws(
    () => decodeProjectFileDetail({ ...detail, size: detail.size + 1 }),
    /invalid project files response/i
  );
  assert.throws(
    () => decodeProjectFileDetail({ ...detail, cwd: "C:/private" }),
    /invalid project files response/i
  );
  const controlContent = "ordinary\u0000hidden";
  assert.throws(
    () => decodeProjectFileDetail({
      ...detail,
      content: controlContent,
      size: new TextEncoder().encode(controlContent).byteLength,
    }),
    /invalid project files response/i
  );
  const deceptiveContent = "left\u202eright";
  assert.throws(
    () => decodeProjectFileDetail({
      ...detail,
      content: deceptiveContent,
      size: new TextEncoder().encode(deceptiveContent).byteLength,
    }),
    /invalid project files response/i
  );
  const bomContent = "\ufeffordinary";
  assert.doesNotThrow(() => decodeProjectFileDetail({
    ...detail,
    content: bomContent,
    size: new TextEncoder().encode(bomContent).byteLength,
  }));
  const interiorBomContent = "ordinary\ufeffhidden";
  assert.throws(
    () => decodeProjectFileDetail({
      ...detail,
      content: interiorBomContent,
      size: new TextEncoder().encode(interiorBomContent).byteLength,
    }),
    /invalid project files response/i
  );
});

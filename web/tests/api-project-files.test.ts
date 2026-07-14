import assert from "node:assert/strict";
import { test } from "vitest";

import { api } from "../src/lib/api.ts";

test("project file APIs send only registered project identity and relative path without caching", async () => {
  const originalFetch = globalThis.fetch;
  const calls: { url: string; init?: RequestInit }[] = [];
  globalThis.fetch = (async (url, init) => {
    calls.push({ url: String(url), init });
    const body = calls.length === 1
      ? {
          path: "src/lib",
          entries: [{ name: "index.ts", type: "file", size: 12 }],
          truncated: false,
        }
      : {
          path: "src/lib/index.ts",
          content: "export {};\n",
          sha256: "a".repeat(64),
          size: 11,
        };
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  try {
    const projectId = "a".repeat(14);
    const controller = new AbortController();
    assert.deepEqual(await api.projectFiles(projectId, "src/lib", controller.signal), {
      path: "src/lib",
      entries: [{ name: "index.ts", type: "file", size: 12 }],
      truncated: false,
    });
    assert.deepEqual(await api.projectFile(projectId, "src/lib/index.ts", controller.signal), {
      path: "src/lib/index.ts",
      content: "export {};\n",
      sha256: "a".repeat(64),
      size: 11,
    });
    assert.deepEqual(calls.map((call) => call.url), [
      `/api/projects/${projectId}/files?path=src%2Flib`,
      `/api/projects/${projectId}/file?path=src%2Flib%2Findex.ts`,
    ]);
    for (const call of calls) {
      assert.equal(call.url.includes("cwd="), false);
      assert.equal(call.init?.cache, "no-store");
      assert.equal(call.init?.signal, controller.signal);
      assert.equal(new Headers(call.init?.headers).get("Accept"), "application/json");
      assert.equal(call.init?.body, undefined);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("project file APIs fail closed on malformed path entries", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify({
    path: "",
    entries: [{ name: "../secret", type: "file", size: 5 }],
    truncated: false,
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })) as typeof fetch;

  try {
    await assert.rejects(() => api.projectFiles("b".repeat(14)), /invalid project files response/i);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("project file APIs reject malformed identities and unsafe paths before fetch", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    throw new Error("fetch must not run");
  }) as typeof fetch;

  try {
    await assert.rejects(() => api.projectFiles("project-one"), /invalid project identity/i);
    await assert.rejects(() => api.projectFile("c".repeat(14), "../secret"), /invalid project file name/i);
    await assert.rejects(() => api.projectFile("c".repeat(14), "C:/secret"), /invalid project file name/i);
    assert.equal(calls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

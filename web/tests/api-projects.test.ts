import assert from "node:assert/strict";
import { test } from "vitest";

import { api } from "../src/lib/api.ts";

test("project APIs encode identities and registration sends only bounded metadata", async () => {
  const originalFetch = globalThis.fetch;
  const calls: { url: string; init?: RequestInit }[] = [];
  const project = {
    id: "project-1",
    workspace: "client/one",
    name: "Portal",
    description: "Client operations",
    root: "C:\\work\\portal",
    status: "active",
    created_at: 1,
    updated_at: 1,
  };
  globalThis.fetch = (async (url, init) => {
    calls.push({ url: String(url), init });
    return new Response(JSON.stringify(calls.length === 1 ? [project] : project), {
      status: calls.length === 2 ? 201 : 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  try {
    assert.deepEqual(await api.projects("client/one"), [project]);
    assert.deepEqual(
      await api.registerProject("client/one", {
        name: "Portal",
        description: "Client operations",
        root: "C:\\work\\portal",
      }),
      project
    );
    assert.deepEqual(await api.project("project/one"), project);

    assert.equal(calls[0]?.url, "/api/workspaces/client%2Fone/projects");
    assert.equal(calls[1]?.url, "/api/workspaces/client%2Fone/projects");
    assert.equal(calls[1]?.init?.method, "POST");
    assert.deepEqual(JSON.parse(String(calls[1]?.init?.body)), {
      name: "Portal",
      description: "Client operations",
      root: "C:\\work\\portal",
    });
    assert.equal(calls[2]?.url, "/api/projects/project%2Fone");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("session listing carries exact workspace and project filter", async () => {
  const originalFetch = globalThis.fetch;
  const urls: string[] = [];
  globalThis.fetch = (async (url) => {
    urls.push(String(url));
    return new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;

  try {
    await api.sessions({ workspace: "client one", projectId: "project/one", limit: 80 });
    await api.sessions({ workspace: "client one", projectId: "unassigned", limit: 80 });
    assert.deepEqual(urls, [
      "/api/sessions?workspace=client+one&project_id=project%2Fone&limit=80",
      "/api/sessions?workspace=client+one&project_id=unassigned&limit=80",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

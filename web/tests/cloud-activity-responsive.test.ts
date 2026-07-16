import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const pageSource = readFileSync(new URL("../src/pages/cloud-activity.tsx", import.meta.url), "utf8");
const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const sidebarSource = readFileSync(new URL("../src/components/sidebar.tsx", import.meta.url), "utf8");

describe("Cloud Activity responsive shell contract", () => {
  it("keeps the master-detail surface stacked through the sidebar breakpoint", () => {
    expect(pageSource).toContain("grid-cols-[minmax(0,1fr)]");
    expect(pageSource).toContain("xl:grid-cols-[minmax(280px,0.8fr)_minmax(0,1.2fr)]");
    expect(pageSource).not.toMatch(/(?:sm|md|lg):grid-cols-\[minmax\(280px/u);
  });

  it("bounds long identities and event text inside narrow panels", () => {
    expect(pageSource).toContain("min-w-0");
    expect(pageSource).toContain("break-all");
    expect(pageSource).toContain("break-words");
    expect(pageSource).toContain("grid-cols-[minmax(0,1fr)_auto]");
    expect(pageSource).toContain("grid-cols-[42px_minmax(0,1fr)]");
    expect(pageSource).toContain("sm:grid-cols-[42px_minmax(0,1fr)_auto]");
  });

  it("contains maximum event sequences and approval identities", () => {
    expect(pageSource).toMatch(/className="[^"]*break-all[^"]*"[^>]*>#\{event\.sequence\}/u);
    expect(pageSource).toMatch(
      /className="[^"]*break-all[^"]*"[^>]*>\{snapshot\.job\.pending_approval\.approval_id\}/u,
    );
  });

  it("draws metric row and column borders at the correct breakpoints", () => {
    expect(pageSource).not.toContain("divide-x divide-line");
    expect(pageSource).toContain('index < 2 && "border-b border-line sm:border-b-0"');
    expect(pageSource).toContain('index % 2 === 0 && "border-r border-line"');
    expect(pageSource).toContain('(index === 1 || index === 2) && "sm:border-r sm:border-line"');
  });

  it("registers one Cloud route and primary navigation entry", () => {
    expect(appSource).toContain('<Route path="/cloud" element={<CloudActivity />} />');
    expect(sidebarSource).toContain('{ to: "/cloud", label: "Cloud activity", icon: Cloud, end: false }');
  });
});

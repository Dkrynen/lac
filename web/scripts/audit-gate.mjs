import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const ALLOWED_RSC_ADVISORY = {
  source: 1124282,
  url: "https://github.com/advisories/GHSA-qwww-vcr4-c8h2",
  id: "GHSA-qwww-vcr4-c8h2",
};
const RSC_MARKERS = [
  "unstable_RSC",
  "RSCHydratedRouter",
  "RSCStaticRouter",
  "createCallServer",
  "routeRSC",
];
const SOURCE_EXTENSIONS = new Set([".js", ".jsx", ".ts", ".tsx"]);

function advisoryId(advisory) {
  return advisory.url?.split("/").at(-1) ?? `source-${advisory.source ?? "unknown"}`;
}

function advisoriesFor(name, vulnerabilities, seen = new Set()) {
  if (seen.has(name)) return [];
  seen.add(name);
  const vulnerability = vulnerabilities[name];
  if (!vulnerability || !Array.isArray(vulnerability.via)) return [];

  return vulnerability.via.flatMap((item) =>
    typeof item === "string"
      ? advisoriesFor(item, vulnerabilities, seen)
      : [item],
  );
}

export function evaluateAudit(report, packageLock, sourceTexts) {
  if (!report || typeof report.vulnerabilities !== "object") {
    throw new Error("npm audit returned no vulnerability report");
  }

  const blocking = Object.entries(report.vulnerabilities)
    .filter(([, vulnerability]) => ["high", "critical"].includes(vulnerability.severity))
    .flatMap(([name]) => advisoriesFor(name, report.vulnerabilities));
  const unique = [...new Map(blocking.map((item) => [advisoryId(item), item])).values()];
  const unapproved = unique.filter(
    (item) =>
      item.source !== ALLOWED_RSC_ADVISORY.source ||
      item.url !== ALLOWED_RSC_ADVISORY.url,
  );

  if (unapproved.length) {
    throw new Error(
      `unapproved high/critical advisories: ${unapproved.map(advisoryId).join(", ")}`,
    );
  }

  if (!unique.length) return { allowed: [] };

  const routerVersion =
    packageLock?.packages?.["node_modules/react-router"]?.version ?? "";
  const match = /^7\.(\d+)\.\d+(?:-|$)/.exec(routerVersion);
  if (!match || Number(match[1]) < 18) {
    throw new Error(
      `GHSA-qwww-vcr4-c8h2 exception is limited to the reviewed React Router v7 range (found ${routerVersion || "none"})`,
    );
  }

  const marker = RSC_MARKERS.find((candidate) =>
    sourceTexts.some((text) => text.includes(candidate)),
  );
  if (marker) {
    throw new Error(`RSC API marker "${marker}" found; GHSA-qwww-vcr4-c8h2 is no longer unreachable`);
  }

  return { allowed: [ALLOWED_RSC_ADVISORY.id] };
}

function collectSourceTexts(directory) {
  const texts = [];
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) {
      texts.push(...collectSourceTexts(path));
    } else if (SOURCE_EXTENSIONS.has(extname(entry))) {
      texts.push(readFileSync(path, "utf8"));
    }
  }
  return texts;
}

function run() {
  const npmCli = process.env.npm_execpath;
  if (!npmCli) {
    throw new Error("npm_execpath is missing; run this gate through `npm run audit:release`");
  }
  const result = spawnSync(process.execPath, [npmCli, "audit", "--audit-level=high", "--json"], {
    encoding: "utf8",
    maxBuffer: 10 * 1024 * 1024,
  });
  if (result.error) throw result.error;

  let report;
  try {
    report = JSON.parse(result.stdout);
  } catch {
    throw new Error(`npm audit did not return JSON: ${result.stderr || result.stdout}`);
  }

  const packageLock = JSON.parse(readFileSync("package-lock.json", "utf8"));
  const verdict = evaluateAudit(report, packageLock, collectSourceTexts("src"));
  if (verdict.allowed.length) {
    console.log(
      `audit-gate OK: allowed unreachable ${verdict.allowed.join(", ")}; unstable RSC APIs are absent`,
    );
  } else {
    console.log("audit-gate OK: no high or critical advisories");
  }
}

if (resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  try {
    run();
  } catch (error) {
    console.error(`audit-gate FAILED: ${error.message}`);
    process.exit(1);
  }
}

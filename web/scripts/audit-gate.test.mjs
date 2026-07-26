import assert from "node:assert/strict";
import test from "node:test";

import { evaluateAudit } from "./audit-gate.mjs";

const RSC_ADVISORY = {
  source: 1124282,
  name: "react-router",
  dependency: "react-router",
  title: "React Router: RSC Mode CSRF Bypass Allows Action Execution Before 400 Response",
  url: "https://github.com/advisories/GHSA-qwww-vcr4-c8h2",
  severity: "high",
  range: ">=7.12.0 <8.3.0",
};

function lockWithRouter(version = "7.18.1") {
  return {
    packages: {
      "node_modules/react-router": { version },
    },
  };
}

test("passes a clean audit", () => {
  assert.deepEqual(evaluateAudit({ vulnerabilities: {} }, lockWithRouter(), []), {
    allowed: [],
  });
});

test("allows only the reviewed React Router RSC advisory when RSC APIs are absent", () => {
  const report = {
    vulnerabilities: {
      "react-router": { severity: "high", via: [RSC_ADVISORY] },
      "react-router-dom": { severity: "high", via: ["react-router"] },
    },
  };

  assert.deepEqual(evaluateAudit(report, lockWithRouter(), ["BrowserRouter Routes"]), {
    allowed: ["GHSA-qwww-vcr4-c8h2"],
  });
});

test("fails closed for an unrelated high advisory", () => {
  const report = {
    vulnerabilities: {
      example: {
        severity: "high",
        via: [{ ...RSC_ADVISORY, source: 999, url: "https://github.com/advisories/GHSA-xxxx-yyyy-zzzz" }],
      },
    },
  };

  assert.throws(
    () => evaluateAudit(report, lockWithRouter(), []),
    /unapproved high\/critical advisories.*GHSA-xxxx-yyyy-zzzz/i,
  );
});

test("fails closed if unstable RSC APIs appear in application source", () => {
  const report = {
    vulnerabilities: {
      "react-router": { severity: "high", via: [RSC_ADVISORY] },
    },
  };

  assert.throws(
    () => evaluateAudit(report, lockWithRouter(), ["const router = unstable_RSCHydratedRouter();"]),
    /RSC API marker/i,
  );
});

test("fails closed outside the reviewed React Router v7 range", () => {
  const report = {
    vulnerabilities: {
      "react-router": { severity: "high", via: [RSC_ADVISORY] },
    },
  };

  assert.throws(
    () => evaluateAudit(report, lockWithRouter("8.2.0"), []),
    /reviewed React Router v7 range/i,
  );
});

import { env } from "cloudflare:workers";
import { createExecutionContext } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";

const ORG_ID = "test-org-id";
const ARTIFACT_KEY = "lac-pro/0.1.0/841bb3989d260419644ca920b6cd79cebb03058d310344ec16fed3a9d3161f2e/lac-pro.zip";
const FILENAME = "lac-pro.zip";
const ARTIFACT_SHA256 =
  "841bb3989d260419644ca920b6cd79cebb03058d310344ec16fed3a9d3161f2e";
const LOCAL_PRO_BENEFIT_ID = "benefit_local_pro";
const PRO_CLOUD_BENEFIT_ID = "benefit_pro_cloud";
const VALIDATE_URL =
  "https://api.polar.sh/v1/customer-portal/license-keys/validate";
const ACTIVATE_URL =
  "https://api.polar.sh/v1/customer-portal/license-keys/activate";
const SIGNING_KID = "2026-07-test";
const SIGNING_PRIVATE_KEY =
  "MC4CAQAwBQYDK2VwBCIEIJ1hsZ3v_VpguoRK9JLsLMREScVpezJpGXA7rAMcrn9g"; // pragma: allowlist secret -- RFC 8032 test vector
const SIGNING_PUBLIC_KEY =
  "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"; // pragma: allowlist secret -- RFC 8032 public key
const SIGNING_PUBLIC_KEY_B64URL =
  "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"; // pragma: allowlist secret -- RFC 8032 public key
const DEVICE_ID = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
const DEPLOYMENT_COMMIT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

const ARTIFACT_BYTES = new Uint8Array([
  0x50, 0x4b, 0x03, 0x04,
  ...new TextEncoder().encode(" lac-pro compiled artifact payload"),
]);

function testEnv(): Env {
  return {
    R2_BUCKET: env.R2_BUCKET,
    PRO_GATE_RATE_LIMITER: {
      limit: vi.fn().mockResolvedValue({ success: true }),
    } as unknown as RateLimit,
    POLAR_ORG_ID: ORG_ID,
    POLAR_API_BASE_URL: "https://api.polar.sh/v1",
    ARTIFACT_KEY,
    ARTIFACT_FILENAME: FILENAME,
    ARTIFACT_SHA256,
    LOCAL_PRO_BENEFIT_ID,
    PRO_CLOUD_BENEFIT_ID,
    ENTITLEMENT_SIGNING_KID: SIGNING_KID,
    ENTITLEMENT_SIGNING_PUBLIC_KEY: SIGNING_PUBLIC_KEY_B64URL,
    ENTITLEMENT_SIGNING_PRIVATE_KEY: SIGNING_PRIVATE_KEY,
    CF_VERSION_METADATA: {
      id: "11111111-1111-4111-8111-111111111111",
      tag: DEPLOYMENT_COMMIT,
      timestamp: "2026-07-13T00:00:00.000Z",
    },
  };
}

function post(body: unknown, raw = false, path = "/pro/download"): Request {
  return new Request(`https://gate.example${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: raw ? (body as string) : JSON.stringify(body),
  });
}

function invoke(request: Request, workerEnv = testEnv()): Promise<Response> {
  return worker.fetch(request, workerEnv, createExecutionContext());
}

let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  await env.R2_BUCKET.delete(ARTIFACT_KEY);
  await env.R2_BUCKET.put(ARTIFACT_KEY, ARTIFACT_BYTES);
  fetchSpy = vi.fn();
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function polarReplies(bodyText: string, status = 200, contentType = "application/json") {
  fetchSpy.mockResolvedValue(
    new Response(bodyText, { status, headers: { "Content-Type": contentType } }),
  );
}

function granted(benefitId = LOCAL_PRO_BENEFIT_ID) {
  polarReplies(JSON.stringify({ status: "granted", benefit_id: benefitId }));
}

function decodeSegment(segment: string): Record<string, unknown> {
  const normalized = segment.replaceAll("-", "+").replaceAll("_", "/");
  return JSON.parse(atob(normalized + "=".repeat((-normalized.length) & 3)));
}

async function verifyReceipt(token: string) {
  const [header, payload, signature] = token.split(".");
  const publicKey = Uint8Array.from(
    SIGNING_PUBLIC_KEY.match(/../g) ?? [],
    (byte) => Number.parseInt(byte, 16),
  );
  const key = await crypto.subtle.importKey(
    "raw", publicKey, { name: "Ed25519" }, false, ["verify"],
  );
  const normalized = signature!.replaceAll("-", "+").replaceAll("_", "/");
  const signatureBytes = Uint8Array.from(
    atob(normalized + "=".repeat((-normalized.length) & 3)),
    (char) => char.charCodeAt(0),
  );
  const verified = await crypto.subtle.verify(
    { name: "Ed25519" }, key, signatureBytes,
    new TextEncoder().encode(`${header}.${payload}`),
  );
  return {
    verified,
    header: decodeSegment(header!),
    claims: decodeSegment(payload!),
  };
}

describe("LAC Pro gate - signed entitlement receipts", () => {
  it("activates at Polar and returns a product-bound Ed25519 receipt", async () => {
    fetchSpy.mockResolvedValue(new Response(JSON.stringify({
      id: "activation-42",
      license_key: {
        status: "granted",
        benefit_id: LOCAL_PRO_BENEFIT_ID,
        expires_at: null,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    const res = await invoke(post(
      { license_key: "private-local-key", label: "duan-pc", device_id: DEVICE_ID },
      false,
      "/pro/entitlements/activate",
    ));

    expect(res.status).toBe(200);
    const { receipt } = await res.json() as { receipt: string };
    const decoded = await verifyReceipt(receipt);
    expect(decoded.verified).toBe(true);
    expect(decoded.header).toEqual({ alg: "EdDSA", kid: SIGNING_KID, typ: "LAC-ENTITLEMENT" });
    expect(decoded.claims).toMatchObject({
      v: 2,
      iss: "lac-pro-gate",
      aud: "lac-pro-desktop",
      product: "lac-pro",
      plan: "pro_local",
      benefit_id: LOCAL_PRO_BENEFIT_ID,
      activation_id: "activation-42",
      device_id: DEVICE_ID,
    });
    expect(String(decoded.claims.sub)).toMatch(/^sha256:[0-9a-f]{64}$/);
    expect(receipt).not.toContain("private-local-key");
    expect(fetchSpy).toHaveBeenCalledWith(
      ACTIVATE_URL,
      expect.objectContaining({
        body: JSON.stringify({
          key: "private-local-key",
          organization_id: ORG_ID,
          label: "duan-pc",
        }),
      }),
    );
  });

  it("revalidates the exact activation into a renewed Cloud receipt", async () => {
    polarReplies(JSON.stringify({
      status: "granted",
      benefit_id: PRO_CLOUD_BENEFIT_ID,
      expires_at: null,
    }));
    const res = await invoke(post(
      {
        license_key: "private-cloud-key",
        activation_id: "activation-cloud",
        device_id: DEVICE_ID,
      },
      false,
      "/pro/entitlements/validate",
    ));
    expect(res.status).toBe(200);
    const { receipt } = await res.json() as { receipt: string };
    const decoded = await verifyReceipt(receipt);
    expect(decoded.verified).toBe(true);
    expect(decoded.claims.plan).toBe("pro_cloud");
    expect(decoded.claims.activation_id).toBe("activation-cloud");
    expect(decoded.claims.device_id).toBe(DEVICE_ID);
    expect(fetchSpy).toHaveBeenCalledWith(
      VALIDATE_URL,
      expect.objectContaining({
        body: JSON.stringify({
          key: "private-cloud-key",
          organization_id: ORG_ID,
          activation_id: "activation-cloud",
        }),
      }),
    );
  });

  it("rejects missing activation binding before Polar validation", async () => {
    const res = await invoke(post(
      { license_key: "private-key" }, false, "/pro/entitlements/validate",
    ));
    expect(res.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("rejects missing device binding before Polar validation", async () => {
    const res = await invoke(post(
      { license_key: "private-key", label: "pc" }, false, "/pro/entitlements/activate",
    ));
    expect(res.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("fails closed when the signing secret is absent", async () => {
    const configured = { ...testEnv(), ENTITLEMENT_SIGNING_PRIVATE_KEY: "" };
    const res = await invoke(post(
      { license_key: "must-not-forward", label: "pc", device_id: DEVICE_ID },
      false,
      "/pro/entitlements/activate",
    ), configured);
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "configuration_unavailable" });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("fails closed when the configured public key does not match the signing secret", async () => {
    const configured = {
      ...testEnv(),
      ENTITLEMENT_SIGNING_PUBLIC_KEY: "QkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkI",
    };
    fetchSpy.mockResolvedValue(new Response(JSON.stringify({
      id: "activation-42",
      license_key: {
        status: "granted",
        benefit_id: LOCAL_PRO_BENEFIT_ID,
        expires_at: null,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    const res = await invoke(post(
      { license_key: "private-local-key", label: "pc", device_id: DEVICE_ID },
      false,
      "/pro/entitlements/activate",
    ), configured);

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "receipt_signing_unavailable" });
  });
});

describe("LAC Pro gate - POST /pro/download", () => {
  it("returns the exact version tag on both success and rejection", async () => {
    granted();
    const ok = await invoke(post({ license_key: "valid-key" }));
    expect(ok.headers.get("X-LAC-Deployment-Commit")).toBe(DEPLOYMENT_COMMIT);

    granted("unrelated-benefit");
    const rejected = await invoke(post({ license_key: "wrong-key" }));
    expect(rejected.status).toBe(403);
    expect(rejected.headers.get("X-LAC-Deployment-Commit")).toBe(DEPLOYMENT_COMMIT);
  });

  it("fails closed when version metadata is not an exact commit", async () => {
    const configured = {
      ...testEnv(),
      CF_VERSION_METADATA: { id: "version", tag: "v2.7.0", timestamp: "now" },
    } as Env;
    const res = await invoke(post({ license_key: "must-not-forward" }), configured);
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "deployment_identity_unavailable" });
    expect(res.headers.get("X-LAC-Deployment-Commit")).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
  it("rate limits on an opaque license fingerprint before calling Polar", async () => {
    const limit = vi.fn().mockResolvedValue({ success: false });
    const configuredEnv: Env = {
      ...testEnv(),
      PRO_GATE_RATE_LIMITER: { limit } as unknown as RateLimit,
    };

    const res = await invoke(
      post({ license_key: "rate-limited-private-key" }),
      configuredEnv,
    );

    expect(res.status).toBe(429);
    expect(await res.json()).toEqual({ error: "abuse_rate_limited" });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(limit).toHaveBeenCalledOnce();
    const key = limit.mock.calls[0]?.[0]?.key as string;
    expect(key).toMatch(/^license:[0-9a-f]{64}$/);
    expect(key).not.toContain("rate-limited-private-key");
  });

  it("fails closed when the in-code limiter is unavailable", async () => {
    const configuredEnv: Env = {
      ...testEnv(),
      PRO_GATE_RATE_LIMITER: {
        limit: vi.fn().mockRejectedValue(new Error("limiter unavailable")),
      } as unknown as RateLimit,
    };

    const res = await invoke(post({ license_key: "must-not-reach-polar" }), configuredEnv);

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "abuse_protection_unavailable" });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("streams the R2 artifact for an eligible Local Pro benefit", async () => {
    granted(LOCAL_PRO_BENEFIT_ID);

    const res = await invoke(post({ license_key: "valid-local-key" }));

    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toBe("application/octet-stream");
    expect(res.headers.get("Content-Disposition") ?? "").toContain(FILENAME);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    const bytes = new Uint8Array(await res.arrayBuffer());
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    const digestHex = Array.from(digest, (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");

    expect(res.headers.get("X-LAC-Artifact-SHA256")).toBe(ARTIFACT_SHA256);
    expect(res.headers.get("X-LAC-Artifact-SHA256")).toBe(digestHex);
    expect(bytes).toEqual(ARTIFACT_BYTES);
  });

  it("streams the same artifact for an eligible Pro Cloud benefit", async () => {
    granted(PRO_CLOUD_BENEFIT_ID);

    const res = await invoke(post({ license_key: "valid-cloud-key" }));

    expect(res.status).toBe(200);
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(ARTIFACT_BYTES);
  });

  it("requires both granted status and an explicitly configured benefit", async () => {
    granted("benefit_from_an_unrelated_product");

    const res = await invoke(post({ license_key: "other-product-key" }));

    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "invalid_or_expired" });
  });

  it("rejects a granted response with no benefit_id", async () => {
    polarReplies(JSON.stringify({ status: "granted" }));

    const res = await invoke(post({ license_key: "missing-benefit" }));

    expect(res.status).toBe(403);
  });

  it("rejects missing runtime configuration before sending the key to Polar", async () => {
    granted();
    const malformedEnv: Env = { ...testEnv(), POLAR_ORG_ID: "" };

    const res = await invoke(post({ license_key: "must-not-leave-worker" }), malformedEnv);

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "configuration_unavailable" });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("rejects a mutable or non-hash-bearing artifact key before Polar", async () => {
    const malformedEnv: Env = {
      ...testEnv(),
      ARTIFACT_KEY: "lac-pro/latest/lac-pro.zip",
    };

    const res = await invoke(post({ license_key: "must-not-leave-worker" }), malformedEnv);

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "configuration_unavailable" });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it.each([
    ["empty", ""],
    ["relative path", "../lac-pro.zip"],
    ["Windows path separator", "folder\\lac-pro.zip"],
    ["header quote", 'lac-pro.zip"; filename="other.zip'],
    ["header newline", "lac-pro.zip\r\nX-Leak: yes"],
    ["non-ASCII", "lác-pro.zip"],
    ["leading dot", ".lac-pro.zip"],
    ["wrong extension", "lac-pro.tar.gz"],
    ["suffix after zip", "lac-pro.zip.exe"],
    ["over 128 characters", `${"a".repeat(125)}.zip`],
    ["reserved CON device", "CON.zip"],
    ["reserved NUL device with extra extension", "nul.tar.zip"],
    ["reserved COM device", "COM1.zip"],
    ["reserved LPT device", "lPt9.release.zip"],
  ])("rejects an unsafe %s download filename before Polar", async (_case, filename) => {
    granted();
    const malformedEnv: Env = { ...testEnv(), ARTIFACT_FILENAME: filename };

    const res = await invoke(post({ license_key: "must-not-leave-worker" }), malformedEnv);

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "configuration_unavailable" });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it.each([
    ["ordinary", "lac-pro.zip"],
    ["safe punctuation and uppercase extension", "LAC_Pro-2.6.4.ZIP"],
    ["exactly 128 characters", `${"a".repeat(124)}.zip`],
  ])("serves an artifact with an exact safe %s filename", async (_case, filename) => {
    granted();
    const configuredEnv: Env = {
      ...testEnv(),
      ARTIFACT_FILENAME: filename,
      ARTIFACT_KEY: ARTIFACT_KEY.replace(/[^/]+$/, filename),
    };
    await env.R2_BUCKET.put(configuredEnv.ARTIFACT_KEY, ARTIFACT_BYTES);

    const res = await invoke(post({ license_key: "valid-local-key" }), configuredEnv);

    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Disposition")).toBe(
      `attachment; filename="${filename}"`,
    );
  });

  it("uses the exact Polar validation contract and attaches a timeout signal", async () => {
    granted();

    await invoke(post({ license_key: "abc-123" }));

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(VALIDATE_URL);
    expect(init.method).toBe("POST");
    expect(init.signal).toBeInstanceOf(AbortSignal);

    const headers = new Headers(init.headers as HeadersInit);
    expect(headers.get("User-Agent")).toBe("LAC-Pro-Gate/1.0");
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({
      key: "abc-123",
      organization_id: ORG_ID,
    });
  });

  it("keeps explicit not_granted outcomes at 403", async () => {
    polarReplies(JSON.stringify({ status: "not_granted" }));

    const res = await invoke(post({ license_key: "revoked" }));

    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "invalid_or_expired" });
    expect(res.headers.get("Cache-Control")).toBe("no-store");
  });

  it("keeps an explicit 404 unknown-key response at 403", async () => {
    polarReplies(JSON.stringify({ detail: "License key not found" }), 404);

    const res = await invoke(post({ license_key: "nope" }));

    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "invalid_or_expired" });
  });

  it("maps a malformed upstream body to 503", async () => {
    polarReplies("<html>WAF wall</html>", 200, "text/html");

    const res = await invoke(post({ license_key: "weird" }));

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "validation_unavailable" });
  });

  it("maps Polar 5xx to 503 even when the body is JSON", async () => {
    polarReplies(JSON.stringify({ detail: "temporary outage" }), 503);

    const res = await invoke(post({ license_key: "any" }));

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "validation_unavailable" });
  });

  it("maps Polar network and timeout-style failures to 503", async () => {
    fetchSpy.mockRejectedValue(new DOMException("timed out", "TimeoutError"));

    const res = await invoke(post({ license_key: "any" }));

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "validation_unavailable" });
  });

  it("rejects missing and non-string keys before calling Polar", async () => {
    const missing = await invoke(post({ not_a_key: "x" }));
    const nonString = await invoke(post({ license_key: 12345 }));

    expect(missing.status).toBe(400);
    expect(nonString.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("rejects a license key longer than the bounded maximum", async () => {
    const res = await invoke(post({ license_key: "k".repeat(513) }));

    expect(res.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("rejects an oversized body before parsing or calling Polar", async () => {
    const res = await invoke(post("x".repeat(16_385), true));

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid_request" });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON request body", async () => {
    const res = await invoke(post("this is not json{", true));

    expect(res.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("routes only the exact /pro/download pathname", async () => {
    const trailingSlash = await invoke(post({ license_key: "x" }, false, "/pro/download/"));
    const otherPath = await invoke(post({ license_key: "x" }, false, "/other"));

    expect(trailingSlash.status).toBe(404);
    expect(otherPath.status).toBe(404);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("allows a query string without widening the exact pathname", async () => {
    granted();

    const res = await invoke(post({ license_key: "valid" }, false, "/pro/download?source=app"));

    expect(res.status).toBe(200);
  });

  it("returns 405 only on the exact endpoint when the method is wrong", async () => {
    const req = new Request("https://gate.example/pro/download", { method: "GET" });

    const res = await invoke(req);

    expect(res.status).toBe(405);
    expect(res.headers.get("Allow")).toBe("POST");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("returns 503 when a granted key's artifact is missing", async () => {
    granted();
    await env.R2_BUCKET.delete(ARTIFACT_KEY);

    const res = await invoke(post({ license_key: "valid-key" }));

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "artifact_unavailable" });
  });

  it("never logs the license key", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    fetchSpy.mockRejectedValue(new Error("boom"));

    await invoke(post({ license_key: "SECRET-KEY-123" }));

    const logged = [errSpy, logSpy, warnSpy]
      .flatMap((spy) => spy.mock.calls.flat())
      .map((value) => String(value))
      .join(" ");
    expect(logged).not.toContain("SECRET-KEY-123");

    errSpy.mockRestore();
    logSpy.mockRestore();
    warnSpy.mockRestore();
  });
});

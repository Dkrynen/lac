/**
 * Stateless LAC Pro artifact-delivery gate.
 *
 * POST /pro/download { "license_key": "<polar key>" }
 *
 * Both Local Pro and Pro Cloud customers receive the same compiled private
 * artifact. Polar is the entitlement authority; R2 is the artifact store.
 * License keys are forwarded to Polar and are never logged or persisted here.
 */

const POLAR_VALIDATE_URL =
  "https://api.polar.sh/v1/customer-portal/license-keys/validate";
const POLAR_TIMEOUT_MS = 8_000;
const MAX_REQUEST_BODY_BYTES = 16_384;
const MAX_LICENSE_KEY_CHARS = 512;
const DOWNLOAD_PATH = "/pro/download";
const PUBLIC_CONFIG_PLACEHOLDER = "replace-from-private-operator-notes";

type PolarDecision = "granted" | "invalid" | "unavailable";
type AbuseDecision = "allowed" | "limited" | "unavailable";
type BoundedJsonResult =
  | { ok: true; value: unknown }
  | { ok: false };

function jsonResponse(status: number, body: unknown, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json",
      ...(headers ?? {}),
    },
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function readBoundedJson(request: Request): Promise<BoundedJsonResult> {
  const contentLength = request.headers.get("Content-Length");
  if (contentLength !== null) {
    const declaredBytes = Number(contentLength);
    if (Number.isFinite(declaredBytes) && declaredBytes > MAX_REQUEST_BODY_BYTES) {
      return { ok: false };
    }
  }

  if (request.body === null) {
    return { ok: false };
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      totalBytes += value.byteLength;
      if (totalBytes > MAX_REQUEST_BODY_BYTES) {
        await reader.cancel("request body too large");
        return { ok: false };
      }
      chunks.push(value);
    }
  } catch {
    return { ok: false };
  } finally {
    reader.releaseLock();
  }

  const bytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }

  try {
    const text = new TextDecoder("utf-8", {
      fatal: true,
      ignoreBOM: false,
    }).decode(bytes);
    return { ok: true, value: JSON.parse(text) as unknown };
  } catch {
    return { ok: false };
  }
}

function isEligibleBenefit(benefitId: unknown, env: Env): boolean {
  return (
    typeof benefitId === "string" &&
    benefitId.length > 0 &&
    (benefitId === env.LOCAL_PRO_BENEFIT_ID ||
      benefitId === env.PRO_CLOUD_BENEFIT_ID)
  );
}

function isConfiguredString(value: unknown, maxLength: number): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= maxLength &&
    value !== PUBLIC_CONFIG_PLACEHOLDER
  );
}

function isWindowsReservedFilename(filename: string): boolean {
  const stem = filename.split(".", 1)[0];
  return /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/i.test(stem);
}

function isImmutableArtifactKey(key: string, sha256: string, filename: string): boolean {
  const parts = key.split("/");
  return (
    parts.length >= 4 &&
    parts.every((part) => part.length > 0 && part !== "." && part !== "..") &&
    parts.some((part) => /^v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$/.test(part)) &&
    parts.includes(sha256.toLowerCase()) &&
    parts.at(-1) === filename
  );
}

function hasValidRuntimeConfig(env: Env): boolean {
  const filenameIsSafe =
    isConfiguredString(env.ARTIFACT_FILENAME, 128) &&
    /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(env.ARTIFACT_FILENAME) &&
    env.ARTIFACT_FILENAME.toLowerCase().endsWith(".zip") &&
    !isWindowsReservedFilename(env.ARTIFACT_FILENAME);

  const artifactShaIsSafe =
    typeof env.ARTIFACT_SHA256 === "string" &&
    /^[a-fA-F0-9]{64}$/.test(env.ARTIFACT_SHA256);
  const artifactKeyIsImmutable =
    isConfiguredString(env.ARTIFACT_KEY, 1_024) &&
    filenameIsSafe &&
    artifactShaIsSafe &&
    isImmutableArtifactKey(
      env.ARTIFACT_KEY,
      env.ARTIFACT_SHA256,
      env.ARTIFACT_FILENAME,
    );

  return (
    isConfiguredString(env.POLAR_ORG_ID, 256) &&
    isConfiguredString(env.LOCAL_PRO_BENEFIT_ID, 256) &&
    isConfiguredString(env.PRO_CLOUD_BENEFIT_ID, 256) &&
    env.LOCAL_PRO_BENEFIT_ID !== env.PRO_CLOUD_BENEFIT_ID &&
    artifactKeyIsImmutable &&
    filenameIsSafe &&
    artifactShaIsSafe
  );
}

async function abuseDecision(licenseKey: string, env: Env): Promise<AbuseDecision> {
  try {
    const digest = new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(licenseKey)),
    );
    const fingerprint = Array.from(digest, (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
    const { success } = await env.PRO_GATE_RATE_LIMITER.limit({
      key: `license:${fingerprint}`,
    });
    return success ? "allowed" : "limited";
  } catch {
    return "unavailable";
  }
}

async function polarDecision(licenseKey: string, env: Env): Promise<PolarDecision> {
  let response: Response;
  try {
    response = await fetch(POLAR_VALIDATE_URL, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        // Polar's WAF can reject requests with an absent/default User-Agent.
        "User-Agent": "LAC-Pro-Gate/1.0",
      },
      body: JSON.stringify({
        key: licenseKey,
        organization_id: env.POLAR_ORG_ID,
      }),
      signal: AbortSignal.timeout(POLAR_TIMEOUT_MS),
    });
  } catch {
    return "unavailable";
  }

  if (response.status >= 500 || response.status === 408 || response.status === 429) {
    return "unavailable";
  }

  let data: unknown;
  try {
    data = await response.json();
  } catch {
    return "unavailable";
  }

  if (!isRecord(data)) {
    return "unavailable";
  }

  // Polar uses a 4xx response for an unknown or otherwise invalid key.
  if (response.status >= 400 && response.status < 500) {
    return "invalid";
  }
  if (!response.ok) {
    return "unavailable";
  }

  if (data.status === "granted") {
    return isEligibleBenefit(data.benefit_id, env) ? "granted" : "invalid";
  }
  return typeof data.status === "string" ? "invalid" : "unavailable";
}

export default {
  async fetch(
    request: Request,
    env: Env,
    _ctx: ExecutionContext,
  ): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname !== DOWNLOAD_PATH) {
      return jsonResponse(404, { error: "not_found" });
    }

    if (request.method !== "POST") {
      return jsonResponse(405, { error: "method_not_allowed" }, { Allow: "POST" });
    }

    // Fail before parsing or forwarding a customer's key when public
    // placeholder config (or otherwise malformed config) is still active.
    if (!hasValidRuntimeConfig(env)) {
      return jsonResponse(503, { error: "configuration_unavailable" });
    }

    const parsed = await readBoundedJson(request);
    if (!parsed.ok || !isRecord(parsed.value)) {
      return jsonResponse(400, { error: "invalid_request" });
    }

    const licenseKey = parsed.value.license_key;
    if (
      typeof licenseKey !== "string" ||
      licenseKey.length === 0 ||
      licenseKey.length > MAX_LICENSE_KEY_CHARS
    ) {
      return jsonResponse(400, { error: "invalid_request" });
    }

    const abuse = await abuseDecision(licenseKey, env);
    if (abuse === "unavailable") {
      return jsonResponse(503, { error: "abuse_protection_unavailable" });
    }
    if (abuse === "limited") {
      return jsonResponse(429, { error: "abuse_rate_limited" });
    }

    const decision = await polarDecision(licenseKey, env);
    if (decision === "unavailable") {
      return jsonResponse(503, { error: "validation_unavailable" });
    }
    if (decision !== "granted") {
      return jsonResponse(403, { error: "invalid_or_expired" });
    }

    let object: R2ObjectBody | null;
    try {
      object = await env.R2_BUCKET.get(env.ARTIFACT_KEY);
    } catch {
      return jsonResponse(503, { error: "artifact_unavailable" });
    }
    if (object === null) {
      return jsonResponse(503, { error: "artifact_unavailable" });
    }

    return new Response(object.body, {
      status: 200,
      headers: {
        "Cache-Control": "no-store",
        "Content-Disposition": `attachment; filename="${env.ARTIFACT_FILENAME}"`,
        "Content-Type": "application/octet-stream",
        "X-LAC-Artifact-SHA256": env.ARTIFACT_SHA256,
      },
    });
  },
} satisfies ExportedHandler<Env>;

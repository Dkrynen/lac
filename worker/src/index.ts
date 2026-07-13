/**
 * Stateless LAC Pro artifact-delivery gate.
 *
 * POST /pro/download { "license_key": "<polar key>" }
 *
 * Both Local Pro and Pro Cloud customers receive the same compiled private
 * artifact. Polar is the entitlement authority; R2 is the artifact store.
 * License keys are forwarded to Polar and are never logged or persisted here.
 * Receipt endpoints additionally require an anonymous machine-bound
 * installation ID and sign that exact binding into the receipt.
 */

const POLAR_PRODUCTION_API = "https://api.polar.sh/v1";
const POLAR_SANDBOX_API = "https://sandbox-api.polar.sh/v1";
const POLAR_TIMEOUT_MS = 8_000;
const MAX_REQUEST_BODY_BYTES = 16_384;
const MAX_LICENSE_KEY_CHARS = 512;
const DOWNLOAD_PATH = "/pro/download";
const ACTIVATE_PATH = "/pro/entitlements/activate";
const VALIDATE_PATH = "/pro/entitlements/validate";
const PUBLIC_CONFIG_PLACEHOLDER = "replace-from-private-operator-notes";
const RECEIPT_ISSUER = "lac-pro-gate";
const RECEIPT_AUDIENCE = "lac-pro-desktop";
const RECEIPT_PRODUCT = "lac-pro";
const RECEIPT_TYPE = "LAC-ENTITLEMENT";
const RECEIPT_LIFETIME_SECONDS = 14 * 86_400;

type PaidPlan = "pro_local" | "pro_cloud";
type PolarResult =
  | {
      decision: "granted";
      benefitId: string;
      plan: PaidPlan;
      expiresAt: unknown;
      activationId?: string;
    }
  | { decision: "invalid" | "unavailable" };
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

function planForBenefit(benefitId: unknown, env: Env): PaidPlan | null {
  if (typeof benefitId !== "string" || benefitId.length === 0) return null;
  if (benefitId === env.LOCAL_PRO_BENEFIT_ID) return "pro_local";
  if (benefitId === env.PRO_CLOUD_BENEFIT_ID) return "pro_cloud";
  return null;
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

function isValidEd25519Pkcs8(value: unknown): value is string {
  if (!isConfiguredString(value, 1_024)) return false;
  try {
    const bytes = decodeBase64Url(value);
    const prefix = [
      0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06,
      0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
    ];
    return bytes.length === 48 && prefix.every((byte, index) => bytes[index] === byte);
  } catch {
    return false;
  }
}

function isValidEd25519PublicKey(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    return decodeBase64Url(value).length === 32;
  } catch {
    return false;
  }
}

function isValidDeviceId(value: unknown): value is string {
  if (typeof value !== "string" || value.length !== 43) return false;
  try {
    return decodeBase64Url(value).length === 32;
  } catch {
    return false;
  }
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
    [POLAR_PRODUCTION_API, POLAR_SANDBOX_API].includes(env.POLAR_API_BASE_URL) &&
    isConfiguredString(env.LOCAL_PRO_BENEFIT_ID, 256) &&
    isConfiguredString(env.PRO_CLOUD_BENEFIT_ID, 256) &&
    env.LOCAL_PRO_BENEFIT_ID !== env.PRO_CLOUD_BENEFIT_ID &&
    isConfiguredString(env.ENTITLEMENT_SIGNING_KID, 64) &&
    /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(env.ENTITLEMENT_SIGNING_KID) &&
    isValidEd25519PublicKey(env.ENTITLEMENT_SIGNING_PUBLIC_KEY) &&
    isValidEd25519Pkcs8(env.ENTITLEMENT_SIGNING_PRIVATE_KEY) &&
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

async function polarRequest(
  operation: "activate" | "validate",
  licenseKey: string,
  env: Env,
  options: { label?: string; activationId?: string } = {},
): Promise<PolarResult> {
  let response: Response;
  try {
    const url = `${env.POLAR_API_BASE_URL}/customer-portal/license-keys/${operation}`;
    const body: Record<string, string> = {
      key: licenseKey,
      organization_id: env.POLAR_ORG_ID,
    };
    if (operation === "activate" && options.label) body.label = options.label;
    if (operation === "validate" && options.activationId) {
      body.activation_id = options.activationId;
    }
    response = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        // Polar's WAF can reject requests with an absent/default User-Agent.
        "User-Agent": "LAC-Pro-Gate/1.0",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(POLAR_TIMEOUT_MS),
    });
  } catch {
    return { decision: "unavailable" };
  }

  if (response.status >= 500 || response.status === 408 || response.status === 429) {
    return { decision: "unavailable" };
  }

  let data: unknown;
  try {
    data = await response.json();
  } catch {
    return { decision: "unavailable" };
  }

  if (!isRecord(data)) {
    return { decision: "unavailable" };
  }

  // Polar uses a 4xx response for an unknown or otherwise invalid key.
  if (response.status >= 400 && response.status < 500) {
    return { decision: "invalid" };
  }
  if (!response.ok) {
    return { decision: "unavailable" };
  }

  const details = operation === "activate" && isRecord(data.license_key)
    ? data.license_key
    : data;
  if (details.status === "granted") {
    const plan = planForBenefit(details.benefit_id, env);
    if (plan === null) return { decision: "invalid" };
    const activationId = operation === "activate" && typeof data.id === "string"
      ? data.id
      : options.activationId;
    return {
      decision: "granted",
      benefitId: details.benefit_id as string,
      plan,
      expiresAt: details.expires_at,
      ...(activationId ? { activationId } : {}),
    };
  }
  return typeof details.status === "string"
    ? { decision: "invalid" }
    : { decision: "unavailable" };
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

function decodeBase64Url(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]+$/u.test(value) || value.includes("=")) {
    throw new Error("invalid base64url");
  }
  const binary = atob(value.replaceAll("-", "+").replaceAll("_", "/") + "=".repeat((-value.length) & 3));
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  if (base64Url(bytes) !== value) throw new Error("non-canonical base64url");
  return bytes;
}

async function licenseFingerprint(licenseKey: string): Promise<string> {
  const digest = new Uint8Array(
    await crypto.subtle.digest("SHA-256", new TextEncoder().encode(licenseKey)),
  );
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function providerExpiry(value: unknown, now: number): number {
  if (value === null || value === undefined) return now + RECEIPT_LIFETIME_SECONDS;
  if (typeof value !== "string") throw new Error("invalid provider expiry");
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds)) throw new Error("invalid provider expiry");
  const expiry = Math.floor(milliseconds / 1_000);
  if (expiry <= now) throw new Error("provider entitlement expired");
  return Math.min(expiry, now + RECEIPT_LIFETIME_SECONDS);
}

async function issueReceipt(
  licenseKey: string,
  result: Extract<PolarResult, { decision: "granted" }>,
  activationId: string,
  deviceId: string,
  env: Env,
): Promise<string> {
  const now = Math.floor(Date.now() / 1_000);
  const header = {
    alg: "EdDSA",
    kid: env.ENTITLEMENT_SIGNING_KID,
    typ: RECEIPT_TYPE,
  };
  const claims = {
    v: 2,
    iss: RECEIPT_ISSUER,
    aud: RECEIPT_AUDIENCE,
    product: RECEIPT_PRODUCT,
    sub: `sha256:${await licenseFingerprint(licenseKey)}`,
    plan: result.plan,
    benefit_id: result.benefitId,
    activation_id: activationId,
    device_id: deviceId,
    iat: now,
    nbf: now - 60,
    exp: providerExpiry(result.expiresAt, now),
    jti: crypto.randomUUID(),
  };
  const encoder = new TextEncoder();
  const protectedPart = base64Url(encoder.encode(JSON.stringify(header)));
  const payloadPart = base64Url(encoder.encode(JSON.stringify(claims)));
  const privateKey = await crypto.subtle.importKey(
    "pkcs8",
    decodeBase64Url(env.ENTITLEMENT_SIGNING_PRIVATE_KEY),
    { name: "Ed25519" },
    false,
    ["sign"],
  );
  const signature = new Uint8Array(
    await crypto.subtle.sign(
      { name: "Ed25519" },
      privateKey,
      encoder.encode(`${protectedPart}.${payloadPart}`),
    ),
  );
  const publicKey = await crypto.subtle.importKey(
    "raw",
    decodeBase64Url(env.ENTITLEMENT_SIGNING_PUBLIC_KEY),
    { name: "Ed25519" },
    false,
    ["verify"],
  );
  const matchesConfiguredPublicKey = await crypto.subtle.verify(
    { name: "Ed25519" },
    publicKey,
    signature,
    encoder.encode(`${protectedPart}.${payloadPart}`),
  );
  if (!matchesConfiguredPublicKey) {
    throw new Error("entitlement signing key does not match configured public key");
  }
  return `${protectedPart}.${payloadPart}.${base64Url(signature)}`;
}

function deploymentCommit(env: Env): string | null {
  const tag = env.CF_VERSION_METADATA?.tag;
  return typeof tag === "string" && /^[0-9a-f]{40}$/.test(tag) ? tag : null;
}

async function handleRequest(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (![DOWNLOAD_PATH, ACTIVATE_PATH, VALIDATE_PATH].includes(url.pathname)) {
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

    const isActivation = url.pathname === ACTIVATE_PATH;
    const isValidation = url.pathname === VALIDATE_PATH;
    const label = parsed.value.label;
    const activationId = parsed.value.activation_id;
    const deviceId = parsed.value.device_id;
    if (
      (isActivation && (
        typeof label !== "string" || label.length === 0 || label.length > 256 ||
        [...label].some((char) => char.codePointAt(0)! < 0x20)
      )) ||
      (isValidation && (
        typeof activationId !== "string" || activationId.length === 0 || activationId.length > 256 ||
        [...activationId].some((char) => char.codePointAt(0)! < 0x20)
      )) ||
      ((isActivation || isValidation) && !isValidDeviceId(deviceId))
    ) {
      return jsonResponse(400, { error: "invalid_request" });
    }

    const decision = await polarRequest(
      isActivation ? "activate" : "validate",
      licenseKey,
      env,
      {
        ...(isActivation ? { label: label as string } : {}),
        ...(isValidation ? { activationId: activationId as string } : {}),
      },
    );
    if (decision.decision === "unavailable") {
      return jsonResponse(503, { error: "validation_unavailable" });
    }
    if (decision.decision !== "granted") {
      return jsonResponse(403, { error: "invalid_or_expired" });
    }

    if (isActivation || isValidation) {
      const boundActivationId = isActivation ? decision.activationId : activationId;
      if (typeof boundActivationId !== "string" || boundActivationId.length === 0) {
        return jsonResponse(503, { error: "validation_unavailable" });
      }
      try {
        const receipt = await issueReceipt(
          licenseKey,
          decision,
          boundActivationId,
          deviceId as string,
          env,
        );
        return jsonResponse(200, { receipt });
      } catch {
        return jsonResponse(503, { error: "receipt_signing_unavailable" });
      }
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
}

export default {
  async fetch(
    request: Request,
    env: Env,
    _ctx: ExecutionContext,
  ): Promise<Response> {
    const commit = deploymentCommit(env);
    if (commit === null) {
      return jsonResponse(503, { error: "deployment_identity_unavailable" });
    }
    const response = await handleRequest(request, env);
    const headers = new Headers(response.headers);
    headers.set("X-LAC-Deployment-Commit", commit);
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  },
} satisfies ExportedHandler<Env>;

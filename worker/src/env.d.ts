interface Env {
  ENTITLEMENT_SIGNING_KID: string;
  /** Base64url-encoded Ed25519 PKCS#8 key. Wrangler secret; never a var. */
  ENTITLEMENT_SIGNING_PRIVATE_KEY: string;
}

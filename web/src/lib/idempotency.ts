/**
 * Idempotency key management.
 *
 * `IdempotencyMiddleware` on the backend rejects any mutating request without an
 * `Idempotency-Key` header. The key must identify a *logical user action*, not an HTTP attempt:
 * retrying a failed send has to replay the same key so the server can collapse the duplicate.
 *
 * Scope naming convention: "<verb>:<resource>:<local-id>", e.g. "send-message:<uuid>".
 */

const MAX_ENTRIES = 256;
const keys = new Map<string, string>();

export function idempotencyKey(scope: string): string {
  const existing = keys.get(scope);
  if (existing !== undefined) return existing;

  const key = crypto.randomUUID();

  // Bounded, oldest-first eviction. A long session must not grow this without limit.
  if (keys.size >= MAX_ENTRIES) {
    const oldest = keys.keys().next();
    if (!oldest.done) keys.delete(oldest.value);
  }

  keys.set(scope, key);
  return key;
}

/** Call once an action has definitively succeeded or has been abandoned. */
export function releaseIdempotencyKey(scope: string): void {
  keys.delete(scope);
}

/** Test and dev helper. Not used in the running app. */
export function clearIdempotencyKeys(): void {
  keys.clear();
}

import type { Api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema.gen";
import { fieldErrorsFromBody, messageFromBody, retryAfterSeconds, type FieldErrors } from "@/lib/api/errors";
import type { AuditRequestBody } from "@/lib/audit-request";

export type AuditOut = components["schemas"]["AuditOut"];

export type CreateAuditOutcome =
  | { kind: "created"; audit: AuditOut }
  /** 200: the same Idempotency-Key and body were sent before. This is the original audit, not an error. */
  | { kind: "repeat"; audit: AuditOut }
  | { kind: "invalid"; message: string; fieldErrors: FieldErrors }
  | { kind: "rate_limited"; message: string; retryAfterSeconds: number | null }
  | { kind: "unavailable"; message: string }
  | { kind: "unauthorized"; message: string }
  | { kind: "error"; message: string };

export async function createAudit(api: Api, body: AuditRequestBody, idempotencyKey: string): Promise<CreateAuditOutcome> {
  let result;
  try {
    result = await api.POST("/audits", { body, params: { header: { "Idempotency-Key": idempotencyKey } } });
  } catch {
    return { kind: "error", message: "Could not reach the server. Check your connection and try again." };
  }
  const { data, error, response } = result;
  const status = response.status;
  if ((status === 202 || status === 200) && data) {
    return { kind: status === 200 ? "repeat" : "created", audit: data };
  }
  const body_: unknown = error;
  switch (status) {
    case 422:
      return {
        kind: "invalid",
        message: messageFromBody(body_, "The server rejected this audit request."),
        fieldErrors: fieldErrorsFromBody(body_),
      };
    case 429:
      return {
        kind: "rate_limited",
        message: messageFromBody(body_, "Too many requests, or the daily budget is used up."),
        retryAfterSeconds: retryAfterSeconds(response.headers),
      };
    case 503:
    case 502:
      return {
        kind: "unavailable",
        message: messageFromBody(body_, "The audit could not be queued. Nothing was lost: try again."),
      };
    case 401:
      return { kind: "unauthorized", message: "The server rejected the API key. Check AUDIT_API_KEY on the web server." };
    default:
      return { kind: "error", message: messageFromBody(body_, `Unexpected response (HTTP ${status}).`) };
  }
}

/**
 * One Idempotency-Key per submission attempt. The same key is reused while the body is unchanged
 * (a retry after a failure, a double click); a changed body gets a new key, because the server
 * rejects a reused key with a different body. Forget the key after a success.
 */
export function createKeyManager(generate: () => string = () => crypto.randomUUID()) {
  let key: string | undefined;
  let fingerprint: string | undefined;
  return {
    keyFor(body: AuditRequestBody): string {
      const next = JSON.stringify(body);
      if (key === undefined || fingerprint !== next) {
        key = generate();
        fingerprint = next;
      }
      return key;
    },
    reset(): void {
      key = undefined;
      fingerprint = undefined;
    },
  };
}

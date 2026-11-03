import { getApi } from "@/lib/api/client";
import { messageFromBody } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema.gen";
import type { PendingReview, SendResult, VerifyResult } from "@/lib/review-outbox";

type ReviewDetail = components["schemas"]["ReviewDetail"];

/** Server clock vs. browser clock: a review counts as "mine" if it is newer than the click minus this. */
const CLOCK_SLACK_MS = 120_000;

/**
 * POST one review. The reviewer is not sent: the API takes it from the authenticated key.
 * 404, 409 and 422 are final answers (nothing to retry); everything else may succeed later.
 */
export async function sendReview(entry: PendingReview, options: { keepalive: boolean }): Promise<SendResult> {
  try {
    const { response, error } = await getApi().POST("/mentions/{run_id}/review", {
      params: { path: { run_id: entry.runId } },
      body: entry.request,
      keepalive: options.keepalive,
    });
    if (response.status === 201) return { kind: "ok" };
    const status = response.status;
    if (status === 404 || status === 409 || status === 422) {
      return { kind: "rejected", message: messageFromBody(error, `The server refused this review (HTTP ${status}).`) };
    }
    const fallback =
      status === 401
        ? "The server rejected the API key. Check AUDIT_API_KEY on the web server."
        : status === 429
          ? "Too many requests. Try again shortly."
          : `The server could not save this review (HTTP ${status}).`;
    return { kind: "retry", message: messageFromBody(error, fallback) };
  } catch {
    return { kind: "retry", message: "Could not reach the server." };
  }
}

/** Does the run's latest review look like the one we were about to send? */
export function reviewMatches(review: ReviewDetail | null | undefined, entry: PendingReview): boolean {
  if (!review) return false;
  const sentAt = Date.parse(review.timestamp);
  return (
    review.action === entry.request.action &&
    (review.corrected_value ?? null) === (entry.request.corrected_value ?? null) &&
    review.reason === entry.request.reason &&
    Number.isFinite(sentAt) &&
    sentAt >= entry.createdAt - CLOCK_SLACK_MS
  );
}

/** Used before re-sending an entry whose first attempt may have reached the server. */
export async function verifyReview(entry: PendingReview): Promise<VerifyResult> {
  try {
    const { data, response } = await getApi().GET("/runs/{run_id}", { params: { path: { run_id: entry.runId } } });
    if (!response.ok || !data) return "unknown";
    return reviewMatches(data.review, entry) ? "delivered" : "not_delivered";
  } catch {
    return "unknown";
  }
}

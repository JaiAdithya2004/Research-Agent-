import type { components } from "@/lib/api/schema.gen";

export type ReviewAction = components["schemas"]["ReviewIn"]["action"];
export type CorrectionKind = "mentioned" | "not_mentioned" | "position";

/** The body sent to POST /mentions/{run_id}/review. The reviewer is never part of it: the server knows. */
export interface ReviewRequest {
  action: ReviewAction;
  reason: string;
  corrected_value?: string;
}

export interface ReviewDraft {
  action: ReviewAction;
  correction: CorrectionKind;
  position: string;
  reason: string;
}

export interface DraftErrors {
  reason?: string;
  position?: string;
}

export type DraftResult = { ok: true; request: ReviewRequest } | { ok: false; errors: DraftErrors };

export const MAX_REASON = 2000;

export function validateDraft(draft: ReviewDraft): DraftResult {
  const errors: DraftErrors = {};
  const reason = draft.reason.trim();
  if (reason === "") errors.reason = "Give a reason. Every decision is recorded with one.";
  else if (reason.length > MAX_REASON) errors.reason = `The reason can be at most ${MAX_REASON} characters.`;

  let corrected: string | undefined;
  if (draft.action === "correct") {
    if (draft.correction === "position") {
      const raw = draft.position.trim();
      if (/^[1-9]\d*$/.test(raw)) corrected = `position=${raw}`;
      else errors.position = "Enter a whole number of 1 or more.";
    } else {
      corrected = draft.correction;
    }
  }
  if (Object.keys(errors).length > 0) return { ok: false, errors };
  return { ok: true, request: corrected === undefined ? { action: draft.action, reason } : { action: draft.action, corrected_value: corrected, reason } };
}

export function actionLabel(action: ReviewAction): string {
  return { approve: "Approve", correct: "Correct", reject: "Reject" }[action];
}

/** What each action does to the audit numbers, in plain words. */
export const ACTION_MEANING: Record<ReviewAction, string> = {
  approve: "The extraction is right. It is counted as extracted.",
  correct: "The extraction is partly wrong. You choose the right value.",
  reject: "The extraction is wrong. The run counts as not mentioned.",
};

export function correctionLabel(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  if (value === "mentioned") return "Mentioned";
  if (value === "not_mentioned") return "Not mentioned";
  const m = /^position=(\d+)$/.exec(value);
  return m ? `Position ${m[1]}` : value;
}

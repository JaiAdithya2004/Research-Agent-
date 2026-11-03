"use client";

import { useState } from "react";
import { useReviewOutbox } from "@/hooks/use-review-outbox";
import { validateDraft, type DraftErrors, type ReviewDraft } from "@/lib/review-draft";

const INITIAL: ReviewDraft = { action: "approve", correction: "mentioned", position: "", reason: "" };

/** Form state for one review. Submitting queues the decision in the outbox (it is sent after the undo window). */
export function useReviewForm(runId: string, label: string, onQueued?: () => void) {
  const outbox = useReviewOutbox();
  const [draft, setDraft] = useState<ReviewDraft>(INITIAL);
  const [errors, setErrors] = useState<DraftErrors>({});
  const [submitted, setSubmitted] = useState(false);

  const update = (patch: Partial<ReviewDraft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setSubmitted(false);
  };

  const submit = () => {
    const result = validateDraft(draft);
    if (!result.ok) {
      setErrors(result.errors);
      return false;
    }
    setErrors({});
    outbox.enqueue({ runId, label, request: result.request });
    setDraft(INITIAL);
    setSubmitted(true);
    onQueued?.();
    return true;
  };

  return { draft, errors, update, submit, submitted };
}

"use client";

import { useId } from "react";
import { FieldError } from "@/components/form-fields";
import { Button } from "@/components/ui/button";
import { Input, Select, inputClass } from "@/components/ui/input";
import { useReviewForm } from "@/hooks/use-review-form";
import { ACTION_MEANING, MAX_REASON, actionLabel, type CorrectionKind, type ReviewAction } from "@/lib/review-draft";
import { cn } from "@/lib/utils";

const ACTIONS: ReviewAction[] = ["approve", "correct", "reject"];

/**
 * Approve, correct or reject one extracted mention. Every action needs a reason. The reviewer is
 * not a field: the server records who you are from the API key.
 */
export function ReviewForm({ runId, label, onQueued }: { runId: string; label: string; onQueued?: () => void }) {
  const uid = useId();
  const { draft, errors, update, submit, submitted } = useReviewForm(runId, label, onQueued);
  const reasonId = `${uid}-reason`;
  const posId = `${uid}-position`;

  return (
    <form
      aria-label={`Review: ${label}`}
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">Your decision</legend>
        <div className="flex flex-wrap gap-2">
          {ACTIONS.map((a) => {
            const id = `${uid}-action-${a}`;
            return (
              <label
                key={a}
                htmlFor={id}
                className={cn(
                  "flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium has-[:checked]:border-ring has-[:checked]:bg-accent has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ring",
                )}
              >
                <input
                  id={id}
                  type="radio"
                  name={`${uid}-action`}
                  value={a}
                  checked={draft.action === a}
                  onChange={() => update({ action: a })}
                  className="size-4 accent-[var(--primary)]"
                />
                {actionLabel(a)}
              </label>
            );
          })}
        </div>
        <p className="text-sm text-muted-foreground">{ACTION_MEANING[draft.action]}</p>
      </fieldset>

      {draft.action === "correct" ? (
        <div className="flex flex-wrap items-start gap-3">
          <div className="flex flex-col gap-1.5">
            <label htmlFor={`${uid}-correction`} className="text-sm font-medium">
              Correct value
            </label>
            <Select
              id={`${uid}-correction`}
              value={draft.correction}
              onChange={(e) => update({ correction: e.target.value as CorrectionKind })}
            >
              <option value="mentioned">Brand is mentioned</option>
              <option value="not_mentioned">Brand is not mentioned</option>
              <option value="position">Brand is at position N</option>
            </Select>
          </div>
          {draft.correction === "position" ? (
            <div className="flex flex-col gap-1.5">
              <label htmlFor={posId} className="text-sm font-medium">
                Position (1 is first)
              </label>
              <Input
                id={posId}
                inputMode="numeric"
                value={draft.position}
                aria-invalid={errors.position ? true : undefined}
                aria-describedby={errors.position ? `${posId}-error` : undefined}
                onChange={(e) => update({ position: e.target.value })}
                className="w-32"
              />
              <FieldError id={`${posId}-error`} message={errors.position} />
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-col gap-1.5">
        <label htmlFor={reasonId} className="text-sm font-medium">
          Reason <span className="text-muted-foreground">(required)</span>
        </label>
        <textarea
          id={reasonId}
          rows={3}
          maxLength={MAX_REASON}
          value={draft.reason}
          aria-required="true"
          aria-invalid={errors.reason ? true : undefined}
          aria-describedby={errors.reason ? `${reasonId}-error` : undefined}
          onChange={(e) => update({ reason: e.target.value })}
          className={cn(inputClass, "h-auto py-2")}
        />
        <FieldError id={`${reasonId}-error`} message={errors.reason} />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit">{actionLabel(draft.action)}</Button>
        <p className="text-sm text-muted-foreground">
          {submitted
            ? "Queued. You can undo it for 5 seconds from the notice at the bottom of the screen."
            : "Saved 5 seconds after you press the button, unless you undo."}
        </p>
      </div>
    </form>
  );
}

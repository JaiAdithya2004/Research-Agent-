"use client";

import { useEffect, useRef } from "react";
import { Plus, Trash2 } from "lucide-react";
import { FieldError } from "@/components/form-fields";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MAX_QUESTIONS } from "@/lib/audit-request";

export function QuestionsEditor({
  questions,
  errors,
  canAdd,
  canRemove,
  onChange,
  onAdd,
  onRemove,
}: {
  questions: string[];
  errors: Record<string, string>;
  canAdd: boolean;
  canRemove: boolean;
  onChange: (index: number, text: string) => void;
  onAdd: () => void;
  onRemove: (index: number) => void;
}) {
  const inputs = useRef<(HTMLInputElement | null)[]>([]);
  const focusIndex = useRef<number | null>(null);

  // After add or remove, keep keyboard focus on a sensible question input.
  useEffect(() => {
    if (focusIndex.current !== null) {
      inputs.current[Math.min(focusIndex.current, questions.length - 1)]?.focus();
      focusIndex.current = null;
    }
  }, [questions.length]);

  return (
    <fieldset className="flex flex-col gap-3">
      <legend className="text-sm font-medium">
        Buyer questions <span className="text-muted-foreground">(required)</span>
      </legend>
      <p id="questions-hint" className="text-sm text-muted-foreground">
        Ask them the way a buyer would. Between 1 and {MAX_QUESTIONS} questions.
      </p>
      <FieldError id="questions-error" message={errors.questions} />
      <ol className="flex flex-col gap-3">
        {questions.map((q, i) => {
          const id = `question-${i}`;
          const errorId = `${id}-error`;
          const error = errors[`questions.${i}`];
          return (
            <li key={i} className="flex flex-col gap-1.5">
              <div className="flex items-end gap-2">
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <label htmlFor={id} className="text-sm font-medium">
                    Question {i + 1}
                  </label>
                  <Input
                    id={id}
                    ref={(el) => {
                      inputs.current[i] = el;
                    }}
                    value={q}
                    placeholder="e.g. What is the best CRM for a small team?"
                    aria-invalid={error ? true : undefined}
                    aria-describedby={error ? errorId : "questions-hint"}
                    onChange={(e) => onChange(i, e.target.value)}
                  />
                </div>
                <Button
                  variant="outline"
                  size="icon"
                  aria-label={`Remove question ${i + 1}`}
                  disabled={!canRemove}
                  onClick={() => {
                    focusIndex.current = i;
                    onRemove(i);
                  }}
                >
                  <Trash2 aria-hidden="true" />
                </Button>
              </div>
              <FieldError id={errorId} message={error} />
            </li>
          );
        })}
      </ol>
      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          disabled={!canAdd}
          onClick={() => {
            focusIndex.current = questions.length;
            onAdd();
          }}
        >
          <Plus aria-hidden="true" />
          Add question
        </Button>
        <p className="text-sm text-muted-foreground" aria-live="polite">
          {questions.length} of {MAX_QUESTIONS} questions{canAdd ? "" : " (maximum reached)"}
        </p>
      </div>
    </fieldset>
  );
}

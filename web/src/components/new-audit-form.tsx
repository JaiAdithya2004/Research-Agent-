"use client";

import { useEffect, useRef } from "react";
import { Play } from "lucide-react";
import { CostEstimate } from "@/components/cost-estimate";
import { RadioGroupField, SelectField, TextField } from "@/components/form-fields";
import { QuestionsEditor } from "@/components/questions-editor";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useAuditForm } from "@/hooks/use-audit-form";
import {
  MAX_REPEATS,
  MIN_REPEATS,
  fieldIdForPath,
  type ResearchDepth,
  type SearchMode,
} from "@/lib/audit-request";

const REPEAT_OPTIONS = Array.from({ length: MAX_REPEATS - MIN_REPEATS + 1 }, (_, i) => {
  const n = MIN_REPEATS + i;
  return { value: String(n), label: `${n} ${n === 1 ? "repeat" : "repeats"}` };
});

export function NewAuditForm() {
  const form = useAuditForm();
  const { values, errors, failure, estimate } = form;
  const summaryRef = useRef<HTMLDivElement>(null);

  // Move focus to the error summary after a rejected submit so keyboard and screen reader users land on it.
  useEffect(() => {
    if (form.attempt > 0) summaryRef.current?.focus();
  }, [form.attempt]);

  const errorEntries = Object.entries(errors);

  return (
    <form
      noValidate
      aria-label="New audit"
      onSubmit={(e) => {
        e.preventDefault();
        form.submit();
      }}
      className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_22rem]"
    >
      <div className="flex flex-col gap-6">
        <div ref={summaryRef} tabIndex={-1} className="flex flex-col gap-3 outline-none" data-testid="error-summary">
          {errorEntries.length > 0 ? (
            <Alert tone="danger" title={`Fix ${errorEntries.length} ${errorEntries.length === 1 ? "problem" : "problems"} to continue`} role="alert">
              <ul className="list-disc pl-5">
                {errorEntries.map(([path, message]) => (
                  <li key={path}>
                    <a href={`#${fieldIdForPath(path)}`} className="underline">
                      {message}
                    </a>
                  </li>
                ))}
              </ul>
            </Alert>
          ) : null}
          {failure && failure.kind === "rate_limited" ? (
            <Alert tone="warning" title="Limit reached" role="alert">
              <p>{failure.message}</p>
              {failure.retryAfterSeconds !== null ? <p>You can try again in about {failure.retryAfterSeconds} seconds.</p> : null}
            </Alert>
          ) : null}
          {failure && failure.kind === "invalid" && errorEntries.length === 0 ? (
            <Alert tone="danger" title="The server rejected this audit" role="alert">
              <p>{failure.message}</p>
            </Alert>
          ) : null}
          {failure && (failure.kind === "unavailable" || failure.kind === "error" || failure.kind === "unauthorized") ? (
            <Alert
              tone="danger"
              title={failure.kind === "unavailable" ? "The audit could not be queued" : "The audit could not be created"}
              role="alert"
            >
              <p>{failure.message}</p>
              {failure.kind !== "unauthorized" ? (
                <p>Trying again is safe: the same request key is reused, so it can never create two audits.</p>
              ) : null}
              {failure.kind !== "unauthorized" ? (
                <Button variant="outline" size="sm" className="mt-2" onClick={form.submit} disabled={form.isSubmitting}>
                  Try again
                </Button>
              ) : null}
            </Alert>
          ) : null}
        </div>

        <TextField
          id="brand"
          label="Brand"
          required
          value={values.brand}
          error={errors.brand}
          onChange={(v) => form.setField("brand", v)}
          placeholder="e.g. Acme"
        />
        <TextField
          id="aliases"
          label="Aliases"
          hint="Other spellings or names, separated by commas."
          value={values.aliases}
          error={errors.aliases}
          onChange={(v) => form.setField("aliases", v)}
        />
        <TextField
          id="category"
          label="Category"
          required
          hint="What you sell, in a few words."
          value={values.category}
          error={errors.category}
          onChange={(v) => form.setField("category", v)}
          placeholder="e.g. project management software"
        />
        <TextField
          id="competitors"
          label="Competitors"
          hint="Separated by commas. Used to find sources that name competitors but not you."
          value={values.competitors}
          error={errors.competitors}
          onChange={(v) => form.setField("competitors", v)}
        />

        <QuestionsEditor
          questions={values.questions}
          errors={errors}
          canAdd={form.canAddQuestion}
          canRemove={form.canRemoveQuestion}
          onChange={form.setQuestion}
          onAdd={form.addQuestion}
          onRemove={form.removeQuestion}
        />

        <SelectField
          id="repeats"
          label="Repeats per question"
          hint="Each question is asked this many times, so you can see whether the answers agree."
          value={String(values.repeats)}
          options={REPEAT_OPTIONS}
          onChange={(v) => form.setField("repeats", Number(v))}
        />

        <RadioGroupField<SearchMode>
          name="search_mode"
          legend="Search mode"
          value={values.search_mode}
          onChange={(v) => form.setField("search_mode", v)}
          options={[
            { value: "tavily", label: "With web search", description: "We search the web and give the model the results, so you see what it says when it has current information." },
            { value: "off", label: "Without search", description: "Only what the model already knows. Cheapest." },
          ]}
        />

        <RadioGroupField<ResearchDepth>
          name="research_depth"
          legend="Research depth"
          value={values.research_depth}
          onChange={(v) => form.setField("research_depth", v)}
          options={[
            {
              value: "cited_pages",
              label: "Read the cited pages",
              description: "Fetch pages the assistants cite and check whether your brand is on them.",
            },
            { value: "none", label: "Answers only", description: "Do not fetch any pages." },
          ]}
        />
      </div>

      <div className="flex flex-col gap-4 lg:sticky lg:top-6 lg:self-start">
        <CostEstimate estimate={estimate} />
        <Button type="submit" size="default" disabled={form.isSubmitting} aria-busy={form.isSubmitting}>
          <Play aria-hidden="true" />
          {form.isSubmitting ? "Starting audit…" : "Start audit"}
        </Button>
        <p className="sr-only" role="status">
          {form.isSubmitting ? "Starting audit" : ""}
        </p>
      </div>
    </form>
  );
}

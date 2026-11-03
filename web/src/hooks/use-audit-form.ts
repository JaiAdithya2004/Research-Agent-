"use client";

import { useCallback, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { getApi } from "@/lib/api/client";
import {
  DEFAULT_FORM_VALUES,
  MAX_QUESTIONS,
  MIN_QUESTIONS,
  validateForm,
  type AuditFormValues,
  type AuditRequestBody,
  type FieldErrors,
} from "@/lib/audit-request";
import { createAudit, createKeyManager, type CreateAuditOutcome } from "@/lib/create-audit";
import { estimateAudit } from "@/lib/estimate";

type Failure = Exclude<CreateAuditOutcome, { kind: "created" } | { kind: "repeat" }>;

export function useAuditForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [values, setValues] = useState<AuditFormValues>(DEFAULT_FORM_VALUES);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [failure, setFailure] = useState<Failure | null>(null);
  const [attempt, setAttempt] = useState(0); // bumps on each rejected submit, to move focus
  const [keys] = useState(() => createKeyManager());

  const mutation = useMutation({
    mutationFn: async (request: AuditRequestBody): Promise<CreateAuditOutcome> =>
      createAudit(getApi(), request, keys.keyFor(request)),
    onSuccess: (outcome) => {
      if (outcome.kind === "created" || outcome.kind === "repeat") {
        keys.reset();
        setFailure(null);
        setErrors({});
        void queryClient.invalidateQueries({ queryKey: ["audits", "list"] });
        router.push(`/audits/${outcome.audit.id}`);
        return;
      }
      setAttempt((n) => n + 1);
      setFailure(outcome);
      if (outcome.kind === "invalid") setErrors(outcome.fieldErrors);
    },
  });

  const setField = useCallback(<K extends keyof AuditFormValues>(field: K, value: AuditFormValues[K]) => {
    setValues((v) => ({ ...v, [field]: value }));
    setErrors((e) => {
      if (!(field in e)) return e;
      const { [field]: _removed, ...rest } = e;
      void _removed;
      return rest;
    });
  }, []);

  const setQuestion = useCallback((index: number, text: string) => {
    setValues((v) => ({ ...v, questions: v.questions.map((q, i) => (i === index ? text : q)) }));
    setErrors((e) => {
      const key = `questions.${index}`;
      if (!(key in e)) return e;
      const { [key]: _removed, ...rest } = e;
      void _removed;
      return rest;
    });
  }, []);

  const addQuestion = useCallback(() => {
    setValues((v) => (v.questions.length >= MAX_QUESTIONS ? v : { ...v, questions: [...v.questions, ""] }));
  }, []);

  const removeQuestion = useCallback((index: number) => {
    setValues((v) => (v.questions.length <= MIN_QUESTIONS ? v : { ...v, questions: v.questions.filter((_, i) => i !== index) }));
    setErrors((e) => Object.fromEntries(Object.entries(e).filter(([k]) => !k.startsWith("questions"))));
  }, []);

  const estimate = useMemo(
    () =>
      estimateAudit({
        questions: values.questions.filter((q) => q.trim() !== "").length,
        repeats: values.repeats,
        searchMode: values.search_mode,
        researchDepth: values.research_depth,
      }),
    [values.questions, values.repeats, values.search_mode, values.research_depth],
  );

  const submit = useCallback(() => {
    if (mutation.isPending) return; // a double click can never send a second request
    setFailure(null);
    const result = validateForm(values);
    if (!result.ok) {
      setErrors(result.errors);
      setAttempt((n) => n + 1);
      return;
    }
    mutation.mutate(result.request);
  }, [mutation, values]);

  return {
    values,
    errors,
    failure,
    attempt,
    estimate,
    isSubmitting: mutation.isPending,
    canAddQuestion: values.questions.length < MAX_QUESTIONS,
    canRemoveQuestion: values.questions.length > MIN_QUESTIONS,
    setField,
    setQuestion,
    addQuestion,
    removeQuestion,
    submit,
  };
}

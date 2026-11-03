import { z } from "zod";
import type { FieldErrors } from "@/lib/api/errors";

/**
 * Client-side mirror of the backend's AuditRequest (app/schemas.py). The rules MUST match;
 * audit-request.openapi.test.ts reads openapi.json and fails if they drift.
 * The backend strips whitespace before checking minimum length ("NonBlank"), so we trim too.
 */
export const SEARCH_MODES = ["off", "grounded", "both"] as const;
export const RESEARCH_DEPTHS = ["none", "cited_pages"] as const;
export type SearchMode = (typeof SEARCH_MODES)[number];
export type ResearchDepth = (typeof RESEARCH_DEPTHS)[number];

export const MAX_QUESTIONS = 10;
export const MIN_QUESTIONS = 1;
export const MIN_REPEATS = 1;
export const MAX_REPEATS = 5;

const nonBlank = (message: string) => z.string().trim().min(1, message);

export const auditRequestSchema = z.strictObject({
  brand: nonBlank("Enter the brand name."),
  aliases: z.array(nonBlank("Aliases cannot be blank.")),
  category: nonBlank("Enter the category."),
  competitors: z.array(nonBlank("Competitors cannot be blank.")),
  questions: z
    .array(nonBlank("A question cannot be blank."))
    .min(MIN_QUESTIONS, "Add at least one question.")
    .max(MAX_QUESTIONS, `Use at most ${MAX_QUESTIONS} questions.`),
  repeats: z
    .number("Choose how many repeats.")
    .int("Repeats must be a whole number.")
    .min(MIN_REPEATS, `Repeats must be at least ${MIN_REPEATS}.`)
    .max(MAX_REPEATS, `Repeats must be at most ${MAX_REPEATS}.`),
  search_mode: z.enum(SEARCH_MODES),
  research_depth: z.enum(RESEARCH_DEPTHS),
});

export type AuditRequestBody = z.infer<typeof auditRequestSchema>;

/** What the form holds: list fields that are typed as free text stay strings until submit. */
export interface AuditFormValues {
  brand: string;
  aliases: string;
  category: string;
  competitors: string;
  questions: string[];
  repeats: number;
  search_mode: SearchMode;
  research_depth: ResearchDepth;
}

export const DEFAULT_FORM_VALUES: AuditFormValues = {
  brand: "",
  aliases: "",
  category: "",
  competitors: "",
  questions: [""],
  repeats: 3,
  search_mode: "grounded",
  research_depth: "cited_pages",
};

/** "a, b\nc" -> ["a","b","c"]: trimmed, blanks dropped, case-insensitive duplicates dropped. */
export function parseList(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of text.split(/[,\n]/)) {
    const item = part.trim();
    const key = item.toLowerCase();
    if (item === "" || seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

export function toRequest(values: AuditFormValues): unknown {
  return {
    brand: values.brand,
    aliases: parseList(values.aliases),
    category: values.category,
    competitors: parseList(values.competitors),
    questions: values.questions,
    repeats: values.repeats,
    search_mode: values.search_mode,
    research_depth: values.research_depth,
  };
}

/** Field errors keyed by dotted path: "brand", "questions", "questions.2". */
export type { FieldErrors };

export type ValidationResult =
  | { ok: true; request: AuditRequestBody }
  | { ok: false; errors: FieldErrors };

export function validateForm(values: AuditFormValues): ValidationResult {
  const parsed = auditRequestSchema.safeParse(toRequest(values));
  if (parsed.success) return { ok: true, request: parsed.data };
  const errors: FieldErrors = {};
  for (const issue of parsed.error.issues) {
    const key = issue.path.join(".");
    if (!(key in errors)) errors[key] = issue.message;
  }
  return { ok: false, errors };
}

/** The DOM id of the control for an error path, so an error summary can link to it. */
export function fieldIdForPath(path: string): string {
  if (path === "questions") return "question-0";
  const q = /^questions\.(\d+)$/.exec(path);
  return q ? `question-${q[1]}` : path;
}

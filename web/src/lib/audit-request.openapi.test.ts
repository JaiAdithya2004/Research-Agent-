// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { auditRequestSchema } from "./audit-request";

/**
 * The form validates with zod. The backend validates with Pydantic. This test reads the
 * AuditRequest schema straight from web/openapi.json and probes the zod schema at every bound
 * the API declares, so the two cannot drift apart silently.
 */

interface JsonSchema {
  type?: string;
  minLength?: number;
  minItems?: number;
  maxItems?: number;
  minimum?: number;
  maximum?: number;
  enum?: string[];
  default?: unknown;
  items?: JsonSchema;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  additionalProperties?: boolean;
}

const spec = JSON.parse(readFileSync(resolve(__dirname, "../../openapi.json"), "utf8")) as {
  components: { schemas: { AuditRequest: JsonSchema } };
};
const api = spec.components.schemas.AuditRequest;
const props = api.properties!;

const valid = {
  brand: "Acme",
  aliases: [],
  category: "crm",
  competitors: [],
  questions: ["q"],
  repeats: 3,
  search_mode: "grounded",
  research_depth: "cited_pages",
};
const accepts = (patch: Record<string, unknown>) => auditRequestSchema.safeParse({ ...valid, ...patch }).success;

describe("zod schema matches AuditRequest in openapi.json", () => {
  it("has exactly the properties the API has, and forbids extras", () => {
    expect(Object.keys(auditRequestSchema.shape).sort()).toEqual(Object.keys(props).sort());
    expect(api.additionalProperties).toBe(false);
    expect(accepts({ extra: 1 })).toBe(false);
  });

  it("requires exactly the required fields", () => {
    for (const field of api.required!) {
      const { [field]: _omitted, ...rest } = valid as Record<string, unknown>;
      void _omitted;
      expect(auditRequestSchema.safeParse(rest).success, `${field} should be required`).toBe(false);
    }
    // optional fields: the form always sends them, but the schema must not demand more than the API
    expect(api.required!.sort()).toEqual(["brand", "category", "questions"]);
  });

  it("enforces questions minItems and maxItems from the spec", () => {
    const q = props.questions!;
    expect(q.minItems).toBe(1);
    expect(q.maxItems).toBe(10);
    const make = (n: number) => Array.from({ length: n }, (_, i) => `q${i}`);
    expect(accepts({ questions: make(q.minItems! - 1) })).toBe(false);
    expect(accepts({ questions: make(q.minItems!) })).toBe(true);
    expect(accepts({ questions: make(q.maxItems!) })).toBe(true);
    expect(accepts({ questions: make(q.maxItems! + 1) })).toBe(false);
  });

  it("enforces repeats bounds from the spec", () => {
    const r = props.repeats!;
    expect(r.type).toBe("integer");
    expect(r.minimum).toBe(1);
    expect(r.maximum).toBe(5);
    expect(accepts({ repeats: r.minimum! - 1 })).toBe(false);
    expect(accepts({ repeats: r.minimum! })).toBe(true);
    expect(accepts({ repeats: r.maximum! })).toBe(true);
    expect(accepts({ repeats: r.maximum! + 1 })).toBe(false);
  });

  it("accepts every allowed enum value and rejects others", () => {
    for (const field of ["search_mode", "research_depth"] as const) {
      const allowed = props[field]!.enum!;
      expect(allowed.length).toBeGreaterThan(0);
      for (const v of allowed) expect(accepts({ [field]: v }), `${field}=${v}`).toBe(true);
      expect(accepts({ [field]: "not-a-value" })).toBe(false);
    }
  });

  it("enforces minLength 1 on text and list items (and trims, like the backend)", () => {
    for (const field of ["brand", "category"] as const) {
      expect(props[field]!.minLength).toBe(1);
      expect(accepts({ [field]: "" })).toBe(false);
      expect(accepts({ [field]: "   " })).toBe(false);
    }
    for (const field of ["aliases", "competitors", "questions"] as const) {
      expect(props[field]!.items!.minLength).toBe(1);
    }
    expect(accepts({ aliases: [""] })).toBe(false);
    expect(accepts({ competitors: [" "] })).toBe(false);
    expect(accepts({ questions: [""] })).toBe(false);
  });

  it("uses the same defaults as the form", async () => {
    const { DEFAULT_FORM_VALUES } = await import("./audit-request");
    expect(props.repeats!.default).toBe(DEFAULT_FORM_VALUES.repeats);
    // The form deliberately differs on search: it offers only web search (run through Tavily) and
    // no search, and the API keeps its own default, "grounded", for other callers.
    expect(DEFAULT_FORM_VALUES.search_mode).toBe("tavily");
    expect(props.search_mode!.enum).toContain(DEFAULT_FORM_VALUES.search_mode);
    expect(props.research_depth!.default).toBe(DEFAULT_FORM_VALUES.research_depth);
  });
});

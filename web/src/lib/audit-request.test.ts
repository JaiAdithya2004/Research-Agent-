import { describe, expect, it } from "vitest";
import { DEFAULT_FORM_VALUES, parseList, validateForm, type AuditFormValues } from "./audit-request";

const valid: AuditFormValues = {
  ...DEFAULT_FORM_VALUES,
  brand: "Acme",
  category: "project management software",
  questions: ["What is the best tool for teams?"],
};

describe("parseList", () => {
  it("splits on commas and newlines, trims, drops blanks and duplicates", () => {
    expect(parseList(" Acme Inc, acme\n\n  ACME inc ,Beta ")).toEqual(["Acme Inc", "acme", "Beta"]);
    expect(parseList("")).toEqual([]);
  });
});

describe("validateForm", () => {
  it("accepts a valid form and trims text", () => {
    const r = validateForm({ ...valid, brand: "  Acme  ", aliases: "Acme Inc, Acme HQ", competitors: "Beta" });
    expect(r).toMatchObject({ ok: true });
    if (r.ok) {
      expect(r.request.brand).toBe("Acme");
      expect(r.request.aliases).toEqual(["Acme Inc", "Acme HQ"]);
      expect(r.request.competitors).toEqual(["Beta"]);
    }
  });

  it("reports each missing required field", () => {
    const r = validateForm({ ...valid, brand: "  ", category: "" });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(Object.keys(r.errors).sort()).toEqual(["brand", "category"]);
    }
  });

  it("flags blank questions by index", () => {
    const r = validateForm({ ...valid, questions: ["ok", "   ", "ok"] });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(Object.keys(r.errors)).toEqual(["questions.1"]);
  });

  it("rejects zero and eleven questions", () => {
    const none = validateForm({ ...valid, questions: [] });
    expect(none.ok).toBe(false);
    const eleven = validateForm({ ...valid, questions: Array.from({ length: 11 }, (_, i) => `q${i}`) });
    expect(eleven.ok).toBe(false);
    if (!eleven.ok) expect(eleven.errors.questions).toMatch(/at most 10/);
    const ten = validateForm({ ...valid, questions: Array.from({ length: 10 }, (_, i) => `q${i}`) });
    expect(ten.ok).toBe(true);
  });

  it("rejects repeats outside 1..5", () => {
    expect(validateForm({ ...valid, repeats: 0 }).ok).toBe(false);
    expect(validateForm({ ...valid, repeats: 6 }).ok).toBe(false);
    expect(validateForm({ ...valid, repeats: 2.5 }).ok).toBe(false);
    expect(validateForm({ ...valid, repeats: 1 }).ok).toBe(true);
    expect(validateForm({ ...valid, repeats: 5 }).ok).toBe(true);
  });
});

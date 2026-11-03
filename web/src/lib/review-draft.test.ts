import { describe, expect, it } from "vitest";
import { actionLabel, correctionLabel, validateDraft } from "./review-draft";

describe("validateDraft", () => {
  it("approve needs a reason and sends no corrected_value", () => {
    expect(validateDraft({ action: "approve", correction: "mentioned", position: "", reason: "  Matches the text.  " })).toEqual({
      ok: true,
      request: { action: "approve", reason: "Matches the text." },
    });
  });

  it("every action requires a reason", () => {
    for (const action of ["approve", "correct", "reject"] as const) {
      const r = validateDraft({ action, correction: "mentioned", position: "", reason: "   " });
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.errors.reason).toMatch(/reason/i);
    }
  });

  it("rejects a reason over 2000 characters", () => {
    const r = validateDraft({ action: "reject", correction: "mentioned", position: "", reason: "x".repeat(2001) });
    expect(r.ok).toBe(false);
  });

  it("reject sends no corrected_value", () => {
    expect(validateDraft({ action: "reject", correction: "position", position: "3", reason: "wrong" })).toEqual({
      ok: true,
      request: { action: "reject", reason: "wrong" },
    });
  });

  it("correct sends mentioned / not_mentioned / position=N", () => {
    const mk = (correction: "mentioned" | "not_mentioned" | "position", position = "") =>
      validateDraft({ action: "correct", correction, position, reason: "because" });
    expect(mk("mentioned")).toMatchObject({ ok: true, request: { corrected_value: "mentioned" } });
    expect(mk("not_mentioned")).toMatchObject({ ok: true, request: { corrected_value: "not_mentioned" } });
    expect(mk("position", " 2 ")).toMatchObject({ ok: true, request: { corrected_value: "position=2" } });
  });

  it("correct with a bad position is an error on the position field", () => {
    for (const position of ["", "0", "-1", "1.5", "abc"]) {
      const r = validateDraft({ action: "correct", correction: "position", position, reason: "because" });
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.errors.position).toBeDefined();
    }
  });
});

describe("labels", () => {
  it("describes actions and corrections in words", () => {
    expect(actionLabel("approve")).toBe("Approve");
    expect(correctionLabel("position=3")).toBe("Position 3");
    expect(correctionLabel("not_mentioned")).toBe("Not mentioned");
    expect(correctionLabel("mentioned")).toBe("Mentioned");
    expect(correctionLabel(null)).toBeNull();
    expect(correctionLabel("odd")).toBe("odd");
  });
});

import { describe, expect, it } from "vitest";
import { GAP_PREVIEW, rankGaps, visibleGaps } from "./gaps";

const e = (domain: string, frequency: number) => ({ domain, frequency, competitors: [], source_ids: [] });

describe("gaps", () => {
  it("ranks by frequency, then domain", () => {
    expect(rankGaps([e("b.com", 2), e("a.com", 2), e("c.com", 9)]).map((x) => x.domain)).toEqual(["c.com", "a.com", "b.com"]);
  });

  it("does not mutate its input", () => {
    const input = [e("b.com", 1), e("a.com", 5)];
    rankGaps(input);
    expect(input[0]!.domain).toBe("b.com");
  });

  it("previews the top domains and says how many are hidden", () => {
    const many = Array.from({ length: GAP_PREVIEW + 4 }, (_, i) => e(`d${i}.com`, 100 - i));
    expect(visibleGaps(many, false)).toMatchObject({ hidden: 4 });
    expect(visibleGaps(many, false).shown).toHaveLength(GAP_PREVIEW);
    expect(visibleGaps(many, true)).toMatchObject({ hidden: 0 });
    expect(visibleGaps(many, true).shown).toHaveLength(GAP_PREVIEW + 4);
    expect(visibleGaps(many.slice(0, 3), false).hidden).toBe(0);
  });
});

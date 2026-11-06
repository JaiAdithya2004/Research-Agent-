import { describe, expect, it } from "vitest";
import { buildSegments } from "./highlight";
import { parseAnswer, type Block } from "./markdown";

const plain = (b: Block) => b.pieces.map((p) => p.text).join("");
const parse = (text: string, spans: { start: number; end: number }[] = []) =>
  parseAnswer(buildSegments(text, spans).segments);

describe("parseAnswer", () => {
  it("recognises headings, rules, bullets, numbered items and paragraphs", () => {
    const blocks = parse("### Title\n\nSome text\n---\n* one\n  * nested\n1. first\n");
    expect(blocks.map((b) => b.kind)).toEqual(["heading", "paragraph", "rule", "bullet", "bullet", "numbered"]);
    expect(plain(blocks[0]!)).toBe("Title");
    expect(blocks[0]).toMatchObject({ level: 3 });
    expect(plain(blocks[3]!)).toBe("one");
    expect(blocks[4]).toMatchObject({ indent: 1 });
    expect(blocks[5]).toMatchObject({ number: "1" });
  });

  it("splits bold text and tidies LaTeX arrows, without ever producing HTML", () => {
    const [b] = parse("a **bold** <b>x</b> $\rightarrow$ b");
    expect(b!.pieces.map((p) => [p.text, p.bold])).toEqual([
      ["a ", false],
      ["bold", true],
      [" <b>x</b> → b", false],
    ]);
  });

  it("keeps the highlight when the quote starts inside the heading marker", () => {
    const text = "### 1. Best: **HubSpot**\nbody";
    const [heading] = parse(text, [{ start: 0, end: 24 }]);
    expect(plain(heading!)).toBe("1. Best: HubSpot");
    expect(heading!.pieces.every((p) => p.highlighted)).toBe(true);
  });

  it("highlights only the part of a line the quote covers", () => {
    const text = "I like HubSpot a lot";
    const [b] = parse(text, [{ start: 7, end: 14 }]);
    expect(b!.pieces.filter((p) => p.highlighted).map((p) => p.text)).toEqual(["HubSpot"]);
    expect(plain(b!)).toBe(text);
  });

  it("drops blank lines between blocks", () => {
    expect(parse("a\n\n\nb").map((b) => b.kind)).toEqual(["paragraph", "paragraph"]);
  });
});

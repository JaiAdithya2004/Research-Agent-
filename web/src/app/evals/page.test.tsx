import { describe, expect, it } from "vitest";
import EvalsPage from "./page";

describe("/evals", () => {
  it("answers 404 unless NEXT_PUBLIC_SHOW_EVALS=1 was set when the app was built", () => {
    expect(() => EvalsPage()).toThrow(/NEXT_HTTP_ERROR_FALLBACK;404|NEXT_NOT_FOUND/);
  });
});

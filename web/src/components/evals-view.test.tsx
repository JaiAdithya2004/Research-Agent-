import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { json, renderWithClient } from "@/test/utils";
import { EvalsView } from "./evals-view";

const fetchMock = vi.fn<typeof fetch>();

const stats = (mean: number, values: number[] = [mean]) => {
  const m = values.reduce((a, b) => a + b, 0) / values.length;
  const sd = Math.sqrt(values.reduce((a, b) => a + (b - m) ** 2, 0) / values.length);
  return { mean, stdev: sd, min: Math.min(...values), max: Math.max(...values), values };
};
let n = 0;
function evalRun(version: string, started: string, over: Record<string, number> = {}, extra: Record<string, unknown> = {}) {
  const base: Record<string, number> = {
    extraction_accuracy: 0.8,
    false_mention_rate: 0.1,
    flag_rate: 0.2,
    source_check_accuracy: 0.9,
    injection_pass_rate: 1,
    total_cost_usd: 1,
    latency_p50_ms: 1000,
    latency_p90_ms: 2000,
    ...over,
  };
  return {
    id: `run-${++n}`,
    prompt_version: version,
    classifier_prompt_version: "v1",
    repeats: 3,
    extractor_model: "gem-1",
    started_at: started,
    counts: { answers: 18, pages: 12, injection: 8 },
    metrics: Object.fromEntries(Object.entries(base).map(([k, v]) => [k, stats(v, [v - 0.01 * v, v, v + 0.01 * v])])),
    per_repeat: [],
    notes: [],
    ...extra,
  };
}
const serve = (runs: unknown[]) => fetchMock.mockResolvedValue(json(200, runs));
const v1 = () => evalRun("v1", "2026-03-01T10:00:00Z");
const v2 = (over: Record<string, number> = {}) =>
  evalRun("v2", "2026-03-02T10:00:00Z", { extraction_accuracy: 0.9, false_mention_rate: 0.05, flag_rate: 0.3, total_cost_usd: 1.1, ...over });

beforeEach(() => {
  n = 0;
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("EvalsView states", () => {
  it("loading", () => {
    fetchMock.mockReturnValue(new Promise(() => undefined));
    renderWithClient(<EvalsView />);
    expect(screen.getByRole("status", { name: "Loading eval runs" })).toBeInTheDocument();
  });

  it("empty explains how to store a run", async () => {
    serve([]);
    renderWithClient(<EvalsView />);
    expect(await screen.findByRole("heading", { name: "No eval runs stored yet" })).toBeInTheDocument();
    expect(screen.getByText(/--save/)).toBeInTheDocument();
  });

  it("error with retry", async () => {
    fetchMock.mockResolvedValueOnce(json(500, { detail: "evals boom" }));
    renderWithClient(<EvalsView />);
    expect(await screen.findByRole("alert", {}, { timeout: 4000 })).toHaveTextContent("evals boom");
    serve([v1()]);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table", { name: /Prompt versions compared/ })).toBeInTheDocument();
  });

  it("asks for the stored runs with a limit, and says when that limit was reached (capped)", async () => {
    const many = Array.from({ length: 100 }, (_, i) => evalRun("v1", `2026-03-${String((i % 28) + 1).padStart(2, "0")}T10:00:00Z`));
    serve(many);
    renderWithClient(<EvalsView />);
    expect(await screen.findByText("Showing the newest runs only")).toBeInTheDocument();
    expect(new URL((fetchMock.mock.calls[0]![0] as Request).url).searchParams.get("limit")).toBe("100");
  });

  it("a single prompt version: no comparison and no winner", async () => {
    serve([v1()]);
    renderWithClient(<EvalsView />);
    expect(await screen.findByText("Only one prompt version so far")).toBeInTheDocument();
    expect(screen.queryByText("Best")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Baseline version")).not.toBeInTheDocument();
  });

  it("partial: missing metrics are dashes and called out", async () => {
    const partial = evalRun("v2", "2026-03-02T10:00:00Z");
    delete (partial.metrics as Record<string, unknown>).source_check_accuracy;
    serve([v1(), partial]);
    renderWithClient(<EvalsView />);
    expect(await screen.findByText("Some metrics are missing")).toBeInTheDocument();
    const row = screen.getByRole("row", { name: /Source-check accuracy/ });
    expect(within(row).getByText("Not measured in this run")).toBeInTheDocument();
  });

  it("shows notes from a run and comparability warnings", async () => {
    serve([v1(), evalRun("v2", "2026-03-02T10:00:00Z", {}, { notes: ["2 pages timed out"], extractor_model: "gem-2", counts: { answers: 20, pages: 12, injection: 8 } })]);
    renderWithClient(<EvalsView />);
    expect(await screen.findByText("Notes from the v2 run")).toBeInTheDocument();
    expect(screen.getByText("2 pages timed out")).toBeInTheDocument();
    expect(screen.getByText("These runs may not be like for like")).toBeInTheDocument();
    expect(screen.getByText(/different extractor models/)).toBeInTheDocument();
  });
});

describe("EvalsView comparison", () => {
  it("shows every metric with mean, spread and range, and marks the best version per metric by direction", async () => {
    serve([v2(), v1()]); // newest first, as the API returns
    renderWithClient(<EvalsView />);
    const table = await screen.findByRole("table", { name: /Prompt versions compared/ });
    const rowOf = (name: RegExp) => within(table).getByRole("row", { name });
    for (const label of ["Extraction accuracy", "False-mention rate", "Flag rate", "Source-check accuracy", "Injection pass rate", "Cost", "Latency P50", "Latency P90"]) {
      expect(within(table).getByRole("rowheader", { name: new RegExp(label) })).toBeInTheDocument();
    }
    const acc = rowOf(/Extraction accuracy/);
    expect(acc).toHaveTextContent("90.0% ±");
    expect(acc).toHaveTextContent(/range 89\.\d% to 90\.\d%/);
    // columns: Metric, v1, v2. v2 wins accuracy (higher) and false mentions (lower); v1 wins flag rate and cost (lower).
    const winnerCol = (name: RegExp) => within(rowOf(name)).getAllByRole("cell").findIndex((c) => c.getAttribute("data-winner") === "true");
    expect(winnerCol(/Extraction accuracy/)).toBe(1);
    expect(winnerCol(/False-mention rate/)).toBe(1);
    expect(winnerCol(/Flag rate/)).toBe(0);
    expect(winnerCol(/^Cost/)).toBe(0);
    expect(winnerCol(/Injection pass rate/)).toBe(-1); // tie: nobody
    expect(within(rowOf(/Extraction accuracy/)).getByText("Best")).toBeInTheDocument();
  });

  it("shows the direction of every metric and is honest about the flag rate", async () => {
    serve([v1(), v2()]);
    renderWithClient(<EvalsView />);
    const table = await screen.findByRole("table", { name: /Prompt versions compared/ });
    expect(within(within(table).getByRole("row", { name: /Extraction accuracy/ })).getByText("Higher is better")).toBeInTheDocument();
    expect(within(within(table).getByRole("row", { name: /Latency P90/ })).getByText("Lower is better")).toBeInTheDocument();
    expect(within(table).getByText(/not an accuracy measure/)).toBeInTheDocument();
  });

  it("shows deltas against the baseline with better/worse in words", async () => {
    serve([v1(), v2()]);
    renderWithClient(<EvalsView />);
    const table = await screen.findByRole("table", { name: /Prompt versions compared/ });
    expect(within(within(table).getByRole("row", { name: /Extraction accuracy/ })).getByText(/\+10\.0 pp/)).toBeInTheDocument();
    expect(within(within(table).getByRole("row", { name: /^Cost/ })).getByText(/\+10%/)).toBeInTheDocument();
    expect(within(within(table).getByRole("row", { name: /^Cost/ })).getByText("(worse)")).toBeInTheDocument();
  });

  it("warns when the winner costs more than the 25% limit, and names the limit it used", async () => {
    serve([v1(), v2({ total_cost_usd: 1.4 })]);
    renderWithClient(<EvalsView />);
    const warn = await screen.findByTestId("cost-warning");
    expect(warn).toHaveTextContent("v2 wins but costs 40% more");
    expect(warn).toHaveTextContent(/limit is 25%/);
    expect(warn).toHaveTextContent(/SPEC acceptance criterion 22/);
  });

  it("no cost warning at +10%", async () => {
    serve([v1(), v2()]);
    renderWithClient(<EvalsView />);
    await screen.findByRole("table", { name: /Prompt versions compared/ });
    expect(screen.queryByTestId("cost-warning")).not.toBeInTheDocument();
  });

  it("changing the baseline recomputes the deltas", async () => {
    serve([v1(), v2()]);
    renderWithClient(<EvalsView />);
    await screen.findByRole("table", { name: /Prompt versions compared/ });
    await userEvent.selectOptions(screen.getByLabelText("Baseline version"), "v2");
    const table = screen.getByRole("table", { name: /Prompt versions compared/ });
    expect(within(within(table).getByRole("row", { name: /Extraction accuracy/ })).getByText(/−10\.0 pp/)).toBeInTheDocument();
  });
});

describe("EvalsView injection pass rate", () => {
  it("shows 100% calmly", async () => {
    serve([v1(), v2()]);
    renderWithClient(<EvalsView />);
    const tile = await screen.findByTestId("injection-v1");
    expect(tile).toHaveAttribute("data-state", "perfect");
    expect(tile).toHaveTextContent("100.0%");
    expect(screen.queryByTestId("injection-alert")).not.toBeInTheDocument();
  });

  it("a rate under 100% is a red alert with words, above the table", async () => {
    serve([v1(), v2({ injection_pass_rate: 0.75 })]);
    renderWithClient(<EvalsView />);
    const alert = await screen.findByTestId("injection-alert");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert).toHaveTextContent("v2 passed 75.0%");
    expect(alert).toHaveTextContent(/Do not ship/);
    const tile = screen.getByTestId("injection-v2");
    expect(tile).toHaveAttribute("data-state", "below");
    expect(tile).toHaveTextContent("Some injection pages changed the output");
    // The alert comes before the comparison table in the document.
    const table = screen.getByRole("table", { name: /Prompt versions compared/ });
    expect(alert.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("a drop against the same version's previous run says so", async () => {
    serve([evalRun("v1", "2026-03-05T10:00:00Z", { injection_pass_rate: 0.875 }), evalRun("v1", "2026-03-01T10:00:00Z", { injection_pass_rate: 1 })]);
    renderWithClient(<EvalsView />);
    const alert = await screen.findByTestId("injection-alert");
    expect(alert).toHaveTextContent("its previous run passed 100.0%");
    expect(screen.getByTestId("injection-v1")).toHaveAttribute("data-state", "dropped");
  });

  it("a missing result is 'not measured', never treated as fine", async () => {
    const r = evalRun("v1", "2026-03-01T10:00:00Z");
    delete (r.metrics as Record<string, unknown>).injection_pass_rate;
    serve([r]);
    renderWithClient(<EvalsView />);
    const tile = await screen.findByTestId("injection-v1");
    expect(tile).toHaveAttribute("data-state", "missing");
    expect(tile).toHaveTextContent("Not measured");
    expect(screen.getByText("Injection pass rate is missing for some versions")).toBeInTheDocument();
  });
});

describe("EvalsView trend charts", () => {
  it("draws one accessible SVG chart per metric, with a data table", async () => {
    serve([v2(), v1(), evalRun("v1", "2026-03-03T10:00:00Z", { extraction_accuracy: 0.85 })]);
    renderWithClient(<EvalsView />);
    const chart = await screen.findByTestId("trend-extraction_accuracy");
    const svg = within(chart).getByRole("img", { name: "Extraction accuracy over time" });
    expect(svg.tagName.toLowerCase()).toBe("svg");
    expect(svg).toHaveAccessibleDescription(/v1: 80\.0%, then 85\.0%\. v2: 90\.0%/);
    expect(chart.querySelectorAll("[data-series]")).toHaveLength(2);
    expect(within(chart).getByText(/Show the numbers \(3 runs\)/)).toBeInTheDocument();
    expect(within(chart).getByText("higher is better")).toBeInTheDocument();
    expect(screen.getAllByRole("figure")).toHaveLength(8);
  });

  it("series differ by more than colour: marker shape and dash", async () => {
    serve([v2(), v1(), evalRun("v1", "2026-03-03T10:00:00Z")]);
    renderWithClient(<EvalsView />);
    const chart = await screen.findByTestId("trend-extraction_accuracy");
    const lines = [...chart.querySelectorAll("[data-series] path")].map((p) => p.getAttribute("stroke-dasharray"));
    // v1 has two points (a line, solid); v2 has one point (no line), so check the legend instead.
    expect(lines).toEqual([null]);
    const legend = within(chart).getByRole("list", { name: /Legend for Extraction accuracy/ });
    const dashes = [...legend.querySelectorAll("line")].map((l) => l.getAttribute("stroke-dasharray"));
    expect(new Set(dashes).size).toBe(2);
  });
});

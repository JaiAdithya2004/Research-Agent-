import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { nav, resetNav } from "@/test/nav";
import { json, renderWithClient } from "@/test/utils";
import { NewAuditForm } from "./new-audit-form";

vi.mock("next/navigation", async () => (await import("@/test/nav")).navigationMock);

const fetchMock = vi.fn<typeof fetch>();
const AUDIT = { id: "a1", status: "running", brand: "Acme" };

beforeEach(() => {
  resetNav();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

async function fillValid(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/^Brand/), "Acme");
  await user.type(screen.getByLabelText(/^Category/), "crm");
  await user.type(screen.getByLabelText("Question 1"), "What is the best CRM?");
}

function sentRequest(call: number): Request {
  return fetchMock.mock.calls[call]![0] as Request;
}

describe("NewAuditForm: questions", () => {
  it("adds up to 10 questions and never fewer than 1", async () => {
    const user = userEvent.setup();
    renderWithClient(<NewAuditForm />);
    expect(screen.getByRole("button", { name: "Remove question 1" })).toBeDisabled();
    const add = screen.getByRole("button", { name: "Add question" });
    for (let i = 0; i < 9; i++) await user.click(add);
    expect(screen.getAllByLabelText(/^Question \d+$/)).toHaveLength(10);
    expect(add).toBeDisabled();
    expect(screen.getByText(/10 of 10 questions \(maximum reached\)/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove question 4" }));
    expect(screen.getAllByLabelText(/^Question \d+$/)).toHaveLength(9);
    expect(add).toBeEnabled();
  });
});

describe("NewAuditForm: live cost estimate", () => {
  it("is labelled as an estimate, lists its assumptions and updates as the form changes", async () => {
    const user = userEvent.setup();
    renderWithClient(<NewAuditForm />);
    expect(screen.getByText(/This is an estimate, not a quote/)).toBeInTheDocument();
    expect(screen.getByText("Assumptions")).toBeInTheDocument();
    // No question text yet: nothing to run.
    expect(screen.getByTestId("estimate-total")).toHaveTextContent("$0.00");
    await user.type(screen.getByLabelText("Question 1"), "q");
    const grounded = screen.getByTestId("estimate-total").textContent;
    expect(grounded).not.toBe("$0.00");
    await user.click(screen.getByLabelText(/^Both/));
    expect(screen.getByTestId("estimate-total").textContent).not.toBe(grounded);
    expect(screen.getByRole("row", { name: /Search queries/ })).toHaveTextContent("18"); // 1 question x 3 repeats x 2 models x 3 queries
    await user.click(screen.getByLabelText(/^Without search/));
    expect(screen.getByRole("row", { name: /Search queries/ })).toHaveTextContent("0");
    expect(screen.getByRole("row", { name: /Page fetches/ })).toHaveTextContent("0");
  });

  it("offers Tavily search and prices it as one search per run", async () => {
    const user = userEvent.setup();
    renderWithClient(<NewAuditForm />);
    await user.type(screen.getByLabelText("Question 1"), "q");
    await user.click(screen.getByLabelText(/^With Tavily search/));
    expect(screen.getByRole("row", { name: /Search queries/ })).toHaveTextContent("6"); // 1 x 3 repeats x 2 models x 1 search
  });

  it("warns when the estimate is over the cap", async () => {
    const user = userEvent.setup();
    renderWithClient(<NewAuditForm />);
    expect(screen.queryByText(/Over the \$0.75 cap/)).not.toBeInTheDocument();
    for (let i = 0; i < 2; i++) await user.click(screen.getByRole("button", { name: "Add question" }));
    for (const n of [1, 2, 3]) await user.type(screen.getByLabelText(`Question ${n}`), `q${n}`);
    expect(screen.getByText(/Over the \$0.75 cap/)).toBeInTheDocument();
    expect(screen.getByText(/stop at the cap and return partial results/)).toBeInTheDocument();
  });
});

describe("NewAuditForm: validation", () => {
  it("shows an error summary and field errors without calling the API", async () => {
    const user = userEvent.setup();
    renderWithClient(<NewAuditForm />);
    await user.click(screen.getByRole("button", { name: "Start audit" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Fix 3 problems");
    expect(screen.getByLabelText(/^Brand/)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Question 1")).toHaveAttribute("aria-invalid", "true");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("error-summary")).toHaveFocus();
    // fixing a field clears its error
    await user.type(screen.getByLabelText(/^Brand/), "A");
    expect(screen.getByLabelText(/^Brand/)).not.toHaveAttribute("aria-invalid");
  });
});

describe("NewAuditForm: submitting", () => {
  it("sends an Idempotency-Key and the parsed body, then opens the audit on 202", async () => {
    fetchMock.mockResolvedValue(json(202, AUDIT));
    const user = userEvent.setup();
    renderWithClient(<NewAuditForm />);
    await fillValid(user);
    await user.type(screen.getByLabelText(/^Aliases/), "Acme Inc, Acme HQ");
    await user.click(screen.getByRole("button", { name: "Start audit" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/audits/a1"));
    const req = sentRequest(0);
    expect(req.method).toBe("POST");
    expect(req.headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(await req.json()).toMatchObject({
      brand: "Acme",
      aliases: ["Acme Inc", "Acme HQ"],
      category: "crm",
      questions: ["What is the best CRM?"],
      repeats: 3,
      search_mode: "grounded",
      research_depth: "cited_pages",
    });
  });

  it("treats 200 (a repeat of the same key) as success and opens the original audit", async () => {
    fetchMock.mockResolvedValue(json(200, { ...AUDIT, id: "original" }));
    const user = userEvent.setup();
    renderWithClient(<NewAuditForm />);
    await fillValid(user);
    await user.click(screen.getByRole("button", { name: "Start audit" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/audits/original"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("a double click sends exactly one request", async () => {
    let release!: (r: Response) => void;
    fetchMock.mockReturnValue(new Promise<Response>((resolve) => (release = resolve)));
    const user = userEvent.setup();
    renderWithClient(<NewAuditForm />);
    await fillValid(user);
    const button = screen.getByRole("button", { name: "Start audit" });
    await user.dblClick(button);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /Starting audit/ })).toBeDisabled();
    release(json(202, AUDIT));
    await waitFor(() => expect(nav.push).toHaveBeenCalled());
  });

  it("shows server field errors on a 422", async () => {
    fetchMock.mockResolvedValue(
      json(422, { detail: [{ loc: ["body", "brand"], msg: "String should have at least 1 character", type: "x" }] }),
    );
    const user = userEvent.setup();
    renderWithClient(<NewAuditForm />);
    await fillValid(user);
    await user.click(screen.getByRole("button", { name: "Start audit" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("String should have at least 1 character");
    expect(screen.getByLabelText(/^Brand/)).toHaveAttribute("aria-invalid", "true");
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("shows the server's message on a 429", async () => {
    fetchMock.mockResolvedValue(json(429, { detail: "Daily budget reached for this key" }, { "retry-after": "90" }));
    const user = userEvent.setup();
    renderWithClient(<NewAuditForm />);
    await fillValid(user);
    await user.click(screen.getByRole("button", { name: "Start audit" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Daily budget reached for this key");
    expect(alert).toHaveTextContent("about 90 seconds");
  });

  it("offers a retry on 503 that reuses the same Idempotency-Key", async () => {
    fetchMock.mockResolvedValueOnce(json(503, { detail: "could not queue the audit" }));
    fetchMock.mockResolvedValueOnce(json(202, AUDIT));
    const user = userEvent.setup();
    renderWithClient(<NewAuditForm />);
    await fillValid(user);
    await user.click(screen.getByRole("button", { name: "Start audit" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("could not queue the audit");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/audits/a1"));
    expect(sentRequest(1).headers.get("idempotency-key")).toBe(sentRequest(0).headers.get("idempotency-key"));
  });

  it("uses a new key when the body changed after a failure", async () => {
    fetchMock.mockResolvedValueOnce(json(503, { detail: "x" }));
    fetchMock.mockResolvedValueOnce(json(202, AUDIT));
    const user = userEvent.setup();
    renderWithClient(<NewAuditForm />);
    await fillValid(user);
    await user.click(screen.getByRole("button", { name: "Start audit" }));
    await screen.findByRole("alert");
    await user.type(screen.getByLabelText(/^Brand/), "2");
    await user.click(screen.getByRole("button", { name: "Start audit" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalled());
    expect(sentRequest(1).headers.get("idempotency-key")).not.toBe(sentRequest(0).headers.get("idempotency-key"));
  });
});

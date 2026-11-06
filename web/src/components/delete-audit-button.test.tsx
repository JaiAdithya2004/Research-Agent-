import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { json, renderWithClient } from "@/test/utils";
import { DeleteAuditButton } from "./delete-audit-button";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

const sent = () => fetchMock.mock.calls.map((c) => c[0] as Request);

describe("DeleteAuditButton", () => {
  it("asks first, then deletes and reports it", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const onDeleted = vi.fn();
    const user = userEvent.setup();
    renderWithClient(<DeleteAuditButton auditId="a1" brand="Acme" onDeleted={onDeleted} />);

    await user.click(screen.getByRole("button", { name: "Delete audit for Acme" }));
    expect(sent()).toHaveLength(0); // nothing happens on the first click
    expect(screen.getByText(/Delete this audit\?/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1));
    expect(sent()[0]!.method).toBe("DELETE");
    expect(new URL(sent()[0]!.url).pathname).toBe("/api/audits/a1");
  });

  it("can be cancelled", async () => {
    const user = userEvent.setup();
    renderWithClient(<DeleteAuditButton auditId="a1" brand="Acme" onDeleted={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Delete audit for Acme" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Delete audit for Acme" })).toBeInTheDocument();
    expect(sent()).toHaveLength(0);
  });

  it("shows the reason when the server refuses", async () => {
    fetchMock.mockResolvedValue(json(409, { detail: "this audit is still running; delete it once it has finished" }));
    const onDeleted = vi.fn();
    const user = userEvent.setup();
    renderWithClient(<DeleteAuditButton auditId="a1" brand="Acme" onDeleted={onDeleted} />);
    await user.click(screen.getByRole("button", { name: "Delete audit for Acme" }));
    await user.click(screen.getByRole("button", { name: "Yes, delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("still running");
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("is disabled while the audit is running", () => {
    renderWithClient(<DeleteAuditButton auditId="a1" brand="Acme" onDeleted={vi.fn()} disabled />);
    expect(screen.getByRole("button", { name: "Delete audit for Acme" })).toBeDisabled();
  });
});

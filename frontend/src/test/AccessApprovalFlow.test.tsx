import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { installMockApi } from "./mockApi";

describe("access proposal and human approval flow", () => {
  it("keeps proposal separate and performs one explicit approved resume", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "true");
    const mock = installMockApi();
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/access-requests"]}><App /></MemoryRouter>);

    expect((await screen.findAllByText("Fictional Finance")).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: /^Create proposal$/ }));

    expect(await screen.findByText("Proposal created")).toBeInTheDocument();
    expect(screen.getByText(/No access change has been executed yet/)).toBeInTheDocument();
    expect(mock.calls.filter((call) => call.path.endsWith("/proposals"))).toHaveLength(1);
    expect(mock.calls.filter((call) => call.path.endsWith("/resume"))).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: /Review approval/ }));
    await user.click(await screen.findByRole("button", { name: /^Approve$/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/This action will execute only after confirmation/i)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /Confirm approval/ }));

    expect(await screen.findByText("Status change completed")).toBeInTheDocument();
    expect(mock.calls.filter((call) => call.path.endsWith("/resume"))).toHaveLength(1);
    expect(screen.getByText(/changed to approved after your confirmation/i)).toBeInTheDocument();
  });

  it("handles a replay conflict without claiming success or exposing raw detail", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "true");
    const mock = installMockApi({ resumeStatus: 409 });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/access-requests"]}><App /></MemoryRouter>);

    await screen.findAllByText("Fictional Finance");
    await user.click(screen.getByRole("button", { name: /^Create proposal$/ }));
    await user.click(await screen.findByRole("button", { name: /Review approval/ }));
    await user.click(await screen.findByRole("button", { name: /^Approve$/ }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: /Confirm approval/ }));

    expect(await screen.findByText("This approval is no longer pending. Refresh the request to view its current state.")).toBeInTheDocument();
    expect(screen.queryByText("internal replay detail")).not.toBeInTheDocument();
    expect(screen.queryByText("Status change completed")).not.toBeInTheDocument();
    expect(mock.calls.filter((call) => call.path.endsWith("/resume"))).toHaveLength(1);
  });

  it("supports deliberate rejection without presenting a controlled write", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "true");
    const mock = installMockApi();
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/access-requests"]}><App /></MemoryRouter>);

    await screen.findAllByText("Fictional Finance");
    await user.click(screen.getByRole("button", { name: /^Create proposal$/ }));
    await user.click(await screen.findByRole("button", { name: /Review approval/ }));
    await user.click(await screen.findByRole("button", { name: /^Reject$/ }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: /Confirm rejection/ }));

    expect(await screen.findByText("Proposal rejected")).toBeInTheDocument();
    expect(screen.getByText("No access change was made.")).toBeInTheDocument();
    expect(mock.calls.find((call) => call.path.endsWith("/resume"))?.body).toEqual({ decision: "reject", comment: null });
  });
});

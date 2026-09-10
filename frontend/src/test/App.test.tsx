import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { installMockApi } from "./mockApi";

describe("OpenWeight application shell", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_DEMO_MODE", "true");
    installMockApi();
  });

  it("renders an employee-first workspace and keeps runtime detail on System", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><App /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "Governance workspace" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Ask a policy question/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View access requests/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Pending access requests" })).toBeInTheDocument();
    expect(screen.queryByText("API process")).not.toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "System" }));
    expect(await screen.findByRole("heading", { name: /one service core/i })).toBeInTheDocument();
    expect(await screen.findByText("1/1 ready")).toBeInTheDocument();
    expect(screen.getByText("Not active")).toBeInTheDocument();
    expect(screen.getByText("Configured model: GPT-OSS 20B")).toBeInTheDocument();
    expect(screen.getByText("Local model runtime")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /MCP 2026-07-28/ })).toBeInTheDocument();
    expect(screen.getByText(/No cloud resources are currently deployed/i)).toBeInTheDocument();
  });

  it("prioritizes tasks by demo persona without treating the UI as authorization", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><App /></MemoryRouter>);

    await screen.findByRole("heading", { name: "Governance workspace" });
    expect(screen.getByText("Prioritized for Operator")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View access requests/ }).closest("article")).toHaveClass("task-card--priority");
    expect(screen.getAllByText(/Policy & Evidence|Access Requests|Approvals/).filter((node) => node.classList.contains("task-card__eyebrow")).map((node) => node.textContent)).toEqual([
      "Policy & Evidence",
      "Access Requests",
      "Approvals",
    ]);

    await user.selectOptions(screen.getByRole("combobox", { name: "Demo persona" }), "Approver");
    expect(screen.getByText("Prioritized for Approver")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review approvals" }).closest("article")).toHaveClass("task-card--priority");

    await user.click(screen.getByRole("link", { name: "Review fic-req-002" }));
    expect(await screen.findByRole("heading", { name: "fic-req-002" })).toBeInTheDocument();
  });

  it("isolates access-request degradation without making the whole workspace unavailable", async () => {
    installMockApi({ listStatus: 503 });
    render(<MemoryRouter><App /></MemoryRouter>);

    expect(await screen.findByText("We couldn't load access requests")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Governance workspace" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Ask a policy question/ })).toBeInTheDocument();
    expect(screen.queryByText("raw-private-detail")).not.toBeInTheDocument();
  });

  it("labels demo persona switching and treats it only as interface guidance", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/access-requests"]}><App /></MemoryRouter>);

    expect((await screen.findAllByText("fic-req-002")).length).toBeGreaterThan(0);
    const selector = screen.getByRole("combobox", { name: "Demo persona" });
    await user.selectOptions(selector, "Reader");
    expect(screen.getByText("Proposal creation requires the Operator role.")).toBeInTheDocument();
    expect(screen.getByText(/Backend permissions remain authoritative/)).toBeInTheDocument();
  });

  it("describes the hosted provider and ephemeral sandbox without claiming inference", async () => {
    installMockApi({
      serviceInfo: {
        deployment_profile: "huggingface",
        inference_provider: "huggingface:groq",
        inference_configured: false,
        inference_state: "unconfigured",
        demo_state: "ephemeral",
      },
    });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/system"]}><App /></MemoryRouter>);

    expect(await screen.findByText("Unconfigured")).toBeInTheDocument();
    expect(screen.getByText("Hugging Face Inference Providers · groq")).toBeInTheDocument();
    expect(screen.getByText("HF_TOKEN is not configured")).toBeInTheDocument();
    expect(screen.getByText(/resets on restart; the full architecture uses PostgreSQL durability/i)).toBeInTheDocument();
    expect(screen.queryByText(/token=/i)).not.toBeInTheDocument();
    await user.tab();
  });
});

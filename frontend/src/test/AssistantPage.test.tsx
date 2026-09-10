import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { installMockApi } from "./mockApi";

function renderAssistant() {
  vi.stubEnv("VITE_DEMO_MODE", "true");
  render(<MemoryRouter initialEntries={["/assistant"]}><App /></MemoryRouter>);
}

describe("AI evidence experience", () => {
  it("submits an arbitrary manually typed policy question", async () => {
    const { calls } = installMockApi();
    const user = userEvent.setup();
    renderAssistant();

    const question = "Can administrator access remain active indefinitely?";
    await user.type(screen.getByLabelText("Question"), question);
    await user.click(screen.getByRole("button", { name: "Ask policy question" }));

    expect(await screen.findByText(/Privileged access requires explicit approval/)).toBeInTheDocument();
    expect(calls).toContainEqual({
      path: "/v1/policy/query/stream",
      method: "POST",
      body: { question },
    });
  });

  it("uses suggested questions as editable examples without auto-submitting", async () => {
    const { calls } = installMockApi();
    const user = userEvent.setup();
    renderAssistant();

    await user.click(screen.getByRole("button", {
      name: /What policy evidence is required before approving privileged access/,
    }));

    expect(screen.getByLabelText("Question")).toHaveValue(
      "What policy evidence is required before approving privileged access?",
    );
    expect(calls.some((call) => call.path === "/v1/policy/query/stream")).toBe(false);
  });

  it("streams final-answer text before finalizing citations and evidence", async () => {
    let complete!: () => void;
    const queryCompletionGate = new Promise<void>((resolve) => { complete = resolve; });
    installMockApi({
      queryCompletionGate,
      queryDeltas: ["Use explicit approval ", "[EPG-ACCESS-001#1]."],
      queryResult: {
        answer: "Use explicit approval [EPG-ACCESS-001#1].",
        citations: ["[EPG-ACCESS-001#1]"],
        evidence: [{
          citation_id: "[EPG-ACCESS-001#1]",
          policy_id: "EPG-ACCESS-001",
          title: "Fictional Policy 4",
          domain: "privileged-access",
          chunk_index: 1,
          content: "Approval required.",
        }],
      },
    });
    const user = userEvent.setup();
    renderAssistant();

    await user.type(screen.getByLabelText("Question"), "What governs access?");
    await user.click(screen.getByRole("button", { name: "Ask policy question" }));

    expect(await screen.findByText("Use explicit approval [EPG-ACCESS-001#1].")).toBeInTheDocument();
    expect(screen.getByText("Citation validation pending")).toBeInTheDocument();
    expect(screen.queryByText("Citations used")).not.toBeInTheDocument();
    expect(screen.queryByText("Fictional Policy 4")).not.toBeInTheDocument();

    complete();
    expect((await screen.findAllByText("Fictional Policy 4")).length).toBe(2);
    expect(screen.getByText("Citations validated against retrieved evidence")).toBeInTheDocument();
    expect(screen.queryByText("Citation validation pending")).not.toBeInTheDocument();
  });

  it("never renders hidden streaming events or private terminal fields", async () => {
    installMockApi({
      queryStreamHiddenText: "private streamed chain of thought marker",
      queryResult: {
        reasoning_content: "private terminal chain of thought marker",
        source_path: "/private/policy/path",
      },
    });
    const user = userEvent.setup();
    renderAssistant();

    await user.type(screen.getByLabelText("Question"), "What governs access?");
    await user.click(screen.getByRole("button", { name: "Ask policy question" }));

    expect((await screen.findAllByText("Fictional Access Standard")).length).toBe(2);
    expect(screen.queryByText(/private streamed chain of thought marker/)).not.toBeInTheDocument();
    expect(screen.queryByText(/private terminal chain of thought marker/)).not.toBeInTheDocument();
    expect(screen.queryByText("/private/policy/path")).not.toBeInTheDocument();
  });

  it("disables duplicate submissions while policy work is active", async () => {
    let releaseSearch!: () => void;
    const queryGate = new Promise<void>((resolve) => { releaseSearch = resolve; });
    installMockApi({ queryGate });
    const user = userEvent.setup();
    renderAssistant();

    await user.type(screen.getByLabelText("Question"), "What governs access?");
    await user.click(screen.getByRole("button", { name: "Ask policy question" }));

    expect(screen.getByText("Searching company policy…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Searching policy…" })).toBeDisabled();
    expect(screen.getByLabelText("Question")).toBeDisabled();
    expect(screen.getByRole("button", { name: /Which controls govern time-bounded/ })).toBeDisabled();
    releaseSearch();
    expect(await screen.findByText(/Privileged access requires explicit approval/)).toBeInTheDocument();
  });

  it("shows readable citation links and focuses evidence without exposing request IDs", async () => {
    installMockApi({ queryResult: {
      answer: "Use **named users**.",
      evidence: [
        {
          citation_id: "[EPG-ACCESS-001#1]",
          policy_id: "EPG-ACCESS-001",
          title: "Fictional Access Standard",
          domain: "privileged-access",
          chunk_index: 1,
          content: "Privileged access requires explicit approval.",
        },
        {
          citation_id: "[EPG-UNRELATED-999#0]",
          policy_id: "EPG-UNRELATED-999",
          title: "Uncited Retrieved Candidate",
          domain: "unrelated",
          chunk_index: 0,
          content: "This retrieved candidate was not used in the answer.",
        },
      ],
    } });
    const scroll = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = scroll;
    const user = userEvent.setup();
    renderAssistant();
    expect(screen.getByText(/Ask a question about company policy or access-governance records/)).toBeInTheDocument();
    expect(screen.queryByText(/fictional/i)).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Question"), "What approval is required?");
    await user.click(screen.getByRole("button", { name: "Ask policy question" }));
    const citation = await screen.findByRole("link", { name: "EPG-ACCESS-001: Fictional Access Standard" });
    expect(screen.getByText("named users").tagName).toBe("STRONG");
    expect(screen.queryByText(/req-query/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\[SOURCE/)).not.toBeInTheDocument();
    expect(screen.queryByText("Uncited Retrieved Candidate")).not.toBeInTheDocument();
    await user.click(citation);
    expect(document.getElementById("policy-evidence-0")).toHaveFocus();
    expect(scroll).toHaveBeenCalled();
  });

  it("shows a professional insufficient-evidence result", async () => {
    installMockApi({
      queryDeltas: [],
      queryResult: {
        status: "insufficient_evidence",
        answer: "OpenWeight could not find sufficient internal policy evidence to answer this question confidently.",
        citations: [],
        citation_valid: false,
        evidence: [],
      },
    });
    const user = userEvent.setup();
    renderAssistant();

    await user.type(screen.getByLabelText("Question"), "An unsupported question");
    await user.click(screen.getByRole("button", { name: "Ask policy question" }));

    expect(await screen.findByText(/could not find sufficient internal policy evidence/)).toBeInTheDocument();
    expect(screen.getByText("No supporting internal policy evidence was found for this question.")).toBeInTheDocument();
  });

  it("turns an interrupted stream into a sanitized retry state", async () => {
    const { calls } = installMockApi({ queryStreamError: true });
    const user = userEvent.setup();
    renderAssistant();

    await user.type(screen.getByLabelText("Question"), "Find evidence");
    await user.click(screen.getByRole("button", { name: "Ask policy question" }));

    expect(await screen.findByText("AI Evidence is unavailable right now")).toBeInTheDocument();
    expect(screen.getByText(/Access requests and approvals remain available/)).toBeInTheDocument();
    expect(screen.queryByText("raw-private-detail")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => {
      expect(calls.filter((call) => call.path === "/v1/policy/query/stream")).toHaveLength(2);
    });
  });

  it("presents public-demo throttling without exposing server details", async () => {
    installMockApi({ queryStatus: 429 });
    const user = userEvent.setup();
    renderAssistant();

    await user.type(screen.getByLabelText("Question"), "What governs access?");
    await user.click(screen.getByRole("button", { name: "Ask policy question" }));

    expect(await screen.findByText(
      "The public demo is receiving several requests. Please try again shortly.",
    )).toBeInTheDocument();
    expect(screen.queryByText("raw-private-detail")).not.toBeInTheDocument();
  });
});

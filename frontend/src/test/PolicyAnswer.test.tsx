import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PolicyAnswer } from "../components/PolicyAnswer";

describe("limited policy Markdown", () => {
  it("renders paragraphs, bullets, bold and emphasis as semantic elements", () => {
    const { container } = render(<PolicyAnswer text={"Use **named users** and *current roles*.\n\n- Require **approval**\n- Review access"} />);
    expect(screen.getByText("named users").tagName).toBe("STRONG");
    expect(screen.getByText("current roles").tagName).toBe("EM");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(container.textContent).not.toContain("**");
  });

  it("never interprets model HTML, images or executable links", () => {
    const { container } = render(<PolicyAnswer text={'<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[click](javascript:alert(1))'} />);
    expect(container.querySelector("script, img, a")).toBeNull();
    expect(container.textContent).toContain("<script>");
  });

  it("uses the same safe formatting while streaming partial text", () => {
    const { rerender } = render(<PolicyAnswer text="Use **named" streaming />);
    expect(screen.getByText("Use **named")).toBeInTheDocument();
    rerender(<PolicyAnswer text="Use **named users**" streaming />);
    expect(screen.getByText("named users").tagName).toBe("STRONG");
  });

  it("activates only validated references after completion and supports keyboard evidence navigation", async () => {
    const user = userEvent.setup();
    const citations = [{ id: "[EPG-HR-002#2]", policyId: "EPG-HR-002", title: "Human Resources and Payroll Access Policy", targetId: "evidence-hr" }];
    const text = "Every request requires approval [EPG-HR-002#2]. Unknown [EPG-UNKNOWN-999#9].";
    const { rerender } = render(<PolicyAnswer text={text} citations={citations} streaming />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    rerender(<><PolicyAnswer text={text} citations={citations} /><section id="evidence-hr" tabIndex={-1}>Original evidence</section></>);
    const link = screen.getByRole("link", { name: "EPG-HR-002: Human Resources and Payroll Access Policy" });
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(link).toHaveTextContent(/^EPG-HR-002$/);
    expect(link).toHaveAttribute("href", "#evidence-hr");
    expect(screen.getByText(/Unknown \[EPG-UNKNOWN-999#9\]/)).toBeInTheDocument();
    const evidence = screen.getByText("Original evidence");
    evidence.scrollIntoView = vi.fn();
    await user.tab();
    expect(link).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(evidence).toHaveFocus();
    expect(evidence.scrollIntoView).toHaveBeenCalledWith({ block: "center" });
  });

  it("formats evidence headings and lists without changing words or interpreting HTML", () => {
    const { container } = render(<PolicyAnswer evidence text={'## Restricted actions\n\nBulk download is disabled by default.\n\n- Require approval\n- Retain <img src=x onerror=alert(1)> as text'} />);
    expect(screen.getByRole("heading", { name: "Restricted actions", level: 4 })).toBeInTheDocument();
    expect(screen.getByText("Bulk download is disabled by default.")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(container.textContent).not.toContain("##");
    expect(container.textContent).toContain("Retain <img src=x onerror=alert(1)> as text");
    expect(container.querySelector("img")).toBeNull();
  });
});

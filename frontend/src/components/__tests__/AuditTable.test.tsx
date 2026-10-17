import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AuditTable } from "@/components/AuditTable";
import { auditToCsv } from "@/lib/csv";
import { auditEntry } from "@/test/fixtures";

const ENTRIES = [
  auditEntry({ action: "approval_decision", actor: "u1", actor_label: "supervisor@demo.com", status: "approved", output: {} }),
  auditEntry({ action: "policy_decision", status: "needs_approval" }),
  auditEntry({ action: "tool_call", tool_name: "create_refund", status: "failure", output: { code: "commerce_unavailable" } }),
  auditEntry({ action: "step:customer_data", status: "success", output: {} }),
];

describe("AuditTable", () => {
  it("shows time, ticket, actor, action, and result for each entry", () => {
    render(<AuditTable entries={ENTRIES} />);
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(4);
    const first = within(rows[0]!);
    expect(first.getByText("TCK-1001")).toBeInTheDocument();
    expect(first.getByText("supervisor@demo.com")).toBeInTheDocument();
    expect(first.getByText("Approval decision")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Policy decision (needs supervisor)")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Tool: create_refund")).toBeInTheDocument();
    expect(within(rows[3]!).getByText("Step: Customer data")).toBeInTheDocument();
  });

  it("colors results: green for done, amber for waiting, red for failures", () => {
    render(<AuditTable entries={ENTRIES} />);
    expect(screen.getByText("approved")).toHaveAttribute("data-tone", "success");
    expect(screen.getByText("needs approval")).toHaveAttribute("data-tone", "warning");
    expect(screen.getByText("failure")).toHaveAttribute("data-tone", "danger");
  });
});

describe("auditToCsv", () => {
  it("writes a header and one quoted row per entry", () => {
    const csv = auditToCsv(ENTRIES.slice(0, 1));
    const [header, row] = csv.trim().split("\r\n");
    expect(header).toBe('"time","ticket","actor","action","tool","result","details"');
    expect(row).toContain('"supervisor@demo.com","approval_decision"');
  });

  it("escapes quotes and neutralizes spreadsheet formulas", () => {
    const csv = auditToCsv([
      auditEntry({ actor_label: "=HYPERLINK(\"x\")", output: { message: 'say "hi"' } }),
    ]);
    expect(csv).toContain(`"'=HYPERLINK(""x"")"`);
    expect(csv).toContain('{""message"":""say \\""hi\\""""}');
  });
});

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { EvalCategoryBars } from "@/components/EvalCategoryBars";
import { categoryRows } from "@/lib/evals";
import { evalSummary } from "@/test/fixtures";

const rows = categoryRows(evalSummary().metrics);

describe("EvalCategoryBars", () => {
  it("draws each share to scale with its value at the tip", () => {
    render(<EvalCategoryBars rows={rows} />);
    const fills = screen.getAllByTestId("bar-fill");
    expect(fills.map((fill) => fill.style.width)).toEqual(["100%", "100%", "50%"]);
    expect(screen.getByLabelText("One item of several: 1 of 2 passed, 50.0%")).toHaveTextContent("50.0% 1/2");
  });

  it("shows a tooltip on keyboard focus as well as hover", async () => {
    render(<EvalCategoryBars rows={rows} />);
    await userEvent.tab();
    await userEvent.tab();
    await userEvent.tab();
    expect(screen.getByRole("tooltip")).toHaveTextContent("3 of 3 passed Billing question");
    await userEvent.hover(screen.getByLabelText(/^One item of several/));
    expect(screen.getByRole("tooltip")).toHaveTextContent("1 of 2 passed");
  });

  it("offers the same numbers as a table", async () => {
    render(<EvalCategoryBars rows={rows} />);
    await userEvent.click(screen.getByRole("button", { name: "Table" }));
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByText("One item of several").closest("tr")).toHaveTextContent("One item of several1250.0%");
    expect(screen.getByRole("button", { name: "Table" })).toHaveAttribute("aria-pressed", "true");
  });
});

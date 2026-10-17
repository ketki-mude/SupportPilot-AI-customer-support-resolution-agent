import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { EvalOverview } from "@/lib/types";
import Evals from "@/pages/Evals";
import { evalReport, evalSummary } from "@/test/fixtures";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { id: "u1", email: "admin@acme.com", role: "admin", customer_id: null }, logout: vi.fn() }),
}));

function overview(overrides: Partial<EvalOverview> = {}): EvalOverview {
  return {
    current: null,
    runs: [evalSummary()],
    dataset_cases: 80,
    model: "gemini-2.5-flash",
    full_run_estimate_usd: "0.2208",
    ...overrides,
  };
}

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Evals />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Evaluations page", () => {
  beforeEach(() => {
    vi.spyOn(api, "evalOverview").mockResolvedValue(overview());
    vi.spyOn(api, "evalReport").mockResolvedValue(evalReport());
  });

  afterEach(() => vi.restoreAllMocks());

  it("shows the four headline numbers, truncated, with their counts", async () => {
    renderPage();
    const cards = await screen.findByRole("region", { name: "Headline results" });
    expect(within(cards).getByText("98.7%")).toBeInTheDocument(); // 79 of 80, never 98.8%
    expect(within(cards).getByText("79 of 80 cases")).toBeInTheDocument();
    expect(within(cards).getByText("100.0%")).toBeInTheDocument();
    expect(within(cards).getByText("On target (0)")).toBeInTheDocument();
    expect(within(cards).getByText("8.1 s")).toBeInTheDocument();
    expect(screen.getByText(/commit abc1234/)).toBeInTheDocument();
    expect(screen.getByText(/\$0\.104312 actual cost/)).toBeInTheDocument();
  });

  it("lists failed cases with their reasons", async () => {
    renderPage();
    expect(await screen.findByText("Failed cases (1)")).toBeInTheDocument();
    expect(screen.getByText("partial-01")).toBeInTheDocument();
    expect(screen.getByText("approval: expected not required, got the opposite")).toBeInTheDocument();
    expect(screen.queryByText("refund-auto-01")).not.toBeInTheDocument();
  });

  it("flags incomplete runs", async () => {
    vi.spyOn(api, "evalOverview").mockResolvedValue(
      overview({ runs: [evalSummary({ complete: false, stopped_reason: "Model quota exhausted." })] }),
    );
    renderPage();
    expect(await screen.findByText(/Incomplete run\. Model quota exhausted\./)).toBeInTheDocument();
  });

  it("explains the empty state", async () => {
    vi.spyOn(api, "evalOverview").mockResolvedValue(overview({ runs: [] }));
    renderPage();
    expect(await screen.findByText("No evaluation runs yet")).toBeInTheDocument();
  });

  it("shows the estimated cost before starting a run", async () => {
    const start = vi.spyOn(api, "startEval").mockResolvedValue({
      id: "j1",
      status: "running",
      llm: "gemini",
      limit: 10,
      cases_planned: 10,
      cases_done: 0,
      started_at: "2026-10-17T10:00:00Z",
      finished_at: null,
      report_id: null,
      error: null,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Run evaluation" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("about $0.03")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByLabelText(/Full dataset/));
    expect(within(dialog).getByText("about $0.22")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByLabelText(/Offline stand-in/));
    expect(within(dialog).getByText("no cost")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Start 80 cases" }));
    expect(start).toHaveBeenCalledWith("mock", null);
  });

  it("shows progress while a run is going", async () => {
    vi.spyOn(api, "evalOverview").mockResolvedValue(
      overview({
        current: {
          id: "j1",
          status: "running",
          llm: "gemini",
          limit: 10,
          cases_planned: 10,
          cases_done: 3,
          started_at: "2026-10-17T10:00:00Z",
          finished_at: null,
          report_id: null,
          error: null,
        },
      }),
    );
    renderPage();
    expect(await screen.findByRole("progressbar", { name: "Cases scored" })).toHaveAttribute("aria-valuenow", "3");
    expect(screen.getByRole("button", { name: "Run evaluation" })).toBeDisabled();
  });
});

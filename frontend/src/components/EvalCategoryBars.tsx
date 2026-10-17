import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { type CategoryRow, truncatedPercent } from "@/lib/evals";
import { cn } from "@/lib/utils";

interface EvalCategoryBarsProps {
  rows: CategoryRow[];
}

/**
 * Correct resolution per category as meters: one hue, the filled share on a lighter track of
 * the same hue. Each row is the hover and focus target; a table view shows the same numbers.
 */
export function EvalCategoryBars({ rows }: EvalCategoryBarsProps) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const [active, setActive] = useState<string | null>(null);

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-2 px-4 pt-4">
        <div>
          <h2 className="font-semibold">Correct resolution by category</h2>
          <p className="text-xs text-subtle">Cases in each category resolved exactly as expected.</p>
        </div>
        <div className="flex gap-1" role="group" aria-label="Show as">
          {(["chart", "table"] as const).map((option) => (
            <Button
              key={option}
              variant="ghost"
              size="sm"
              aria-pressed={view === option}
              className={view === option ? "bg-muted text-foreground" : undefined}
              onClick={() => setView(option)}
            >
              {option === "chart" ? "Chart" : "Table"}
            </Button>
          ))}
        </div>
      </div>

      {view === "chart" ? (
        <ul className="p-2 sm:p-3" aria-label="Correct resolution by category">
          {rows.map((row) => {
            const rate = truncatedPercent(row.passed, row.cases);
            const share = row.cases ? (row.passed / row.cases) * 100 : 0;
            return (
              <li key={row.category}>
                <div
                  tabIndex={0}
                  aria-label={`${row.name}: ${row.passed} of ${row.cases} passed, ${rate}`}
                  onPointerEnter={() => setActive(row.category)}
                  onPointerLeave={() => setActive(null)}
                  onFocus={() => setActive(row.category)}
                  onBlur={() => setActive(null)}
                  className="relative grid grid-cols-[minmax(0,8.5rem)_1fr_auto] items-center gap-3 rounded-control px-2 py-1.5 hover:bg-muted/60 focus-visible:bg-muted/60 sm:grid-cols-[minmax(0,12rem)_1fr_auto]"
                >
                  <span className="leading-tight text-subtle">{row.name}</span>
                  <span className="relative h-2.5 rounded-r-[4px] bg-accent-soft">
                    <span
                      data-testid="bar-fill"
                      aria-hidden
                      className="absolute inset-y-0 left-0 rounded-r-[4px] bg-accent"
                      style={{ width: `${share}%` }}
                    />
                    {active === row.category ? (
                      // Anchored at the end of the fill, above the bar, so it never covers a label.
                      <span
                        role="tooltip"
                        className={cn(
                          "absolute bottom-full z-10 mb-2 whitespace-nowrap rounded-control border-hairline border-border bg-surface px-2 py-1 text-xs",
                          share > 70 ? "-translate-x-full" : share > 30 ? "-translate-x-1/2" : null,
                        )}
                        style={{ left: `${share}%` }}
                      >
                        <strong className="font-semibold">
                          {row.passed} of {row.cases} passed
                        </strong>{" "}
                        <span className="text-subtle">{row.name}</span>
                      </span>
                    ) : null}
                  </span>
                  <span className="w-24 text-right text-xs tabular-nums">
                    {rate} <span className="text-subtle">{row.passed}/{row.cases}</span>
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="overflow-x-auto p-4">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-subtle">
              <tr className="border-b-hairline border-border">
                <th className="py-2 pr-4 font-medium">Category</th>
                <th className="py-2 pr-4 text-right font-medium">Passed</th>
                <th className="py-2 pr-4 text-right font-medium">Cases</th>
                <th className="py-2 text-right font-medium">Rate</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {rows.map((row) => (
                <tr key={row.category} className="border-b-hairline border-border last:border-0">
                  <td className="py-2 pr-4">{row.name}</td>
                  <td className="py-2 pr-4 text-right">{row.passed}</td>
                  <td className="py-2 pr-4 text-right">{row.cases}</td>
                  <td className="py-2 text-right">{truncatedPercent(row.passed, row.cases)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

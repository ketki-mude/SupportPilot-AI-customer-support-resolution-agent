import { describe, expect, it } from "vitest";

import { cn } from "@/lib/utils";

describe("cn", () => {
  it("keeps the hairline width next to a border color", () => {
    expect(cn("border-hairline border-border")).toBe("border-hairline border-border");
    expect(cn("border-b-hairline border-input")).toBe("border-b-hairline border-input");
  });

  it("still lets later classes override earlier ones in the same group", () => {
    expect(cn("border-hairline", "border-2")).toBe("border-2");
    expect(cn("rounded-control", "rounded-card")).toBe("rounded-card");
    expect(cn("border-border", "border-warning/50")).toBe("border-warning/50");
  });
});

import { devices, expect, type Page, test } from "@playwright/test";

/**
 * Smoke test of the whole product: a customer reports damaged headphones, the agent pauses
 * for approval, a supervisor approves in another browser, and the customer's open chat shows
 * the refund without reloading. Needs the stack running and fresh mock data (`make e2e`).
 */

type Person = "customer" | "agent" | "supervisor";

function password(who: Person): string {
  const value = process.env[`SEED_${who.toUpperCase()}_PASSWORD`];
  if (!value) throw new Error(`Set SEED_${who.toUpperCase()}_PASSWORD for the end-to-end test`);
  return value;
}

async function signIn(page: Page, who: Person): Promise<void> {
  await page.goto("/login");
  await expectAtMostOnePrimaryButton(page);
  await page.getByLabel("Email").fill(`${who}@demo.com`);
  await page.getByLabel("Password").fill(password(who));
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function expectAtMostOnePrimaryButton(page: Page): Promise<void> {
  expect(await page.locator('[data-variant="primary"]').count()).toBeLessThanOrEqual(1);
}

/** Wait for the chat to load, then leave any earlier ticket for a fresh one. */
async function startNewRequest(page: Page): Promise<void> {
  const newRequest = page.getByRole("button", { name: "New request", exact: true });
  const fresh = page.getByText("How can we help today?");
  await expect(newRequest.or(fresh)).toBeVisible();
  if (await newRequest.isVisible()) await newRequest.click();
  await expect(fresh).toBeVisible();
  await expect(page.getByLabel("Message")).toBeFocused();
}

test("a supervisor approves a refund and the customer's chat updates live", async ({ browser }) => {
  const customer = await (await browser.newContext()).newPage();
  const supervisor = await (await browser.newContext()).newPage();

  await signIn(customer, "customer");
  await expect(customer).toHaveURL(/\/chat$/);
  await startNewRequest(customer);
  const box = customer.getByLabel("Message");
  await box.fill("My headphones arrived damaged, I want a refund");
  await box.press("Enter");

  await expect(customer.getByTestId("latest-status")).toHaveText("Waiting for approval", {
    timeout: 60_000,
  });
  await expect(customer.getByText(/asked a supervisor to approve a refund of \$79\.99/)).toBeVisible();
  await expectAtMostOnePrimaryButton(customer);
  const header = await customer.getByText(/Replies in seconds · TCK-\d+/).textContent();
  const ticketNumber = header?.match(/TCK-\d+/)?.[0];
  expect(ticketNumber).toBeTruthy();

  // A reload keeps the session: the refresh cookie restores it, nothing is in localStorage.
  await customer.reload();
  await expect(customer).toHaveURL(/\/chat$/);
  await expect(customer.getByTestId("latest-status")).toHaveText("Waiting for approval");
  expect(await customer.evaluate(() => JSON.stringify(window.localStorage))).toBe("{}");

  await signIn(supervisor, "supervisor");
  await expect(supervisor).toHaveURL(/\/console/);
  await supervisor.getByRole("button", { name: "Needs approval" }).click();
  await supervisor.getByRole("link", { name: new RegExp(ticketNumber!) }).click();
  const bar = supervisor.getByRole("region", { name: "Approval" });
  await expect(bar).toContainText("Refund $79.99");
  await expect(bar).toContainText(/Policy match, agent \d+% sure/);
  await expectAtMostOnePrimaryButton(supervisor);
  await bar.getByPlaceholder("Add a note (optional)").fill("Damage confirmed from the description");
  await bar.getByRole("button", { name: "Approve" }).dblclick(); // an impatient supervisor
  await expect(bar.getByRole("status")).toContainText("Approved");
  await expect(bar.getByRole("alert")).toHaveCount(0);

  // No reload: the customer's chat gets the result over the live event stream.
  await expect(customer.getByText(/I've refunded \$79\.99 to your card ending 4242/)).toBeVisible({
    timeout: 30_000,
  });
  await expect(customer.getByTestId("latest-status")).toHaveText("Resolved");

  // The shop itself confirms one refund, not two.
  const mockUrl = process.env.E2E_MOCK_URL;
  if (mockUrl) {
    const order = await (await fetch(`${mockUrl}/orders/ORD-10001`)).json();
    expect(order.refunded_amount).toBe("79.99");
  }

  await supervisor.goto(`/audit?ticket=${ticketNumber}`);
  await expect(supervisor.getByRole("cell", { name: "supervisor@demo.com" }).first()).toBeVisible();
  await expect(supervisor.getByText("Approval decision").first()).toBeVisible();
  await expectAtMostOnePrimaryButton(supervisor);
});

test("staff take over an escalated ticket and the customer sees it live", async ({ browser }) => {
  const customer = await (await browser.newContext()).newPage();
  const staff = await (await browser.newContext()).newPage();

  await signIn(customer, "customer");
  await startNewRequest(customer);
  const box = customer.getByLabel("Message");
  await box.fill("I'd like a refund for the Nimbus laptop, the screen flickers.");
  await box.press("Enter");
  // $1,299 is over the supervisor limit, so the agent hands it to a specialist.
  await expect(customer.getByTestId("latest-status")).toHaveText("With a specialist", {
    timeout: 60_000,
  });
  await expect(customer.getByText("A specialist is handling this request and will reply here.")).toBeVisible();
  const header = await customer.getByText(/Replies in seconds · TCK-\d+/).textContent();
  const ticketNumber = header?.match(/TCK-\d+/)?.[0];
  expect(ticketNumber).toBeTruthy();

  await signIn(staff, "agent");
  await expect(staff).toHaveURL(/\/console/);
  await staff.getByRole("button", { name: "With a specialist" }).click();
  await staff.getByRole("link", { name: new RegExp(ticketNumber!) }).click();
  const panel = staff.getByRole("region", { name: "Handle this ticket" });

  await panel.getByLabel("Message the customer").fill("Thanks, we're looking at the screen issue now.");
  await panel.getByRole("button", { name: "Send reply" }).click();
  await expect(customer.getByText("Thanks, we're looking at the screen issue now.")).toBeVisible({
    timeout: 15_000,
  });
  await expect(customer.getByText("Support team")).toBeVisible();

  await expect(panel.getByText(/Override needed\./)).toBeVisible();
  const act = panel.getByRole("button", { name: "Override and refund $1,299.00" });
  await expect(act).toBeDisabled();
  await panel.getByLabel(/Reason for the override/).fill("Screen defect confirmed; goodwill refund");
  await expectAtMostOnePrimaryButton(staff);
  await act.dblclick(); // an impatient specialist
  await expect(panel.getByRole("status")).toContainText("Refunded $1,299.00");

  await expect(customer.getByText(/I've refunded \$1299\.00 to your card ending 8811/)).toBeVisible({
    timeout: 15_000,
  });
  await expect(customer.getByTestId("latest-status")).toHaveText("Resolved");
  const mockUrl = process.env.E2E_MOCK_URL;
  if (mockUrl) {
    const order = await (await fetch(`${mockUrl}/orders/ORD-10006`)).json();
    expect(order.refunded_amount).toBe("1299.00");
  }
});

test("the customer chat fits a phone screen", async ({ browser }) => {
  const { defaultBrowserType: _ignored, ...phone } = devices["iPhone 13"];
  const page = await (await browser.newContext(phone)).newPage();
  await signIn(page, "customer");
  await expect(page).toHaveURL(/\/chat$/);
  await startNewRequest(page);

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  const viewport = page.viewportSize()!;
  for (const control of [page.getByLabel("Message"), page.getByRole("button", { name: "Send" })]) {
    const box = await control.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
    expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height);
  }
  await expectAtMostOnePrimaryButton(page);
});

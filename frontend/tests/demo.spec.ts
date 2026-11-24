import { expect, test } from "@playwright/test";

test("reviews an edited reply, simulates sending, completes a task, and rejects archive", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Start demo workspace" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.getByRole("button", { name: "Open inbox" }).click();
  await page
    .getByRole("button", { name: /Order #1042 hasn't arrived/ })
    .click();
  await page.getByRole("button", { name: "Analyze email" }).click();
  await expect(
    page.getByRole("heading", { name: "Message analysis" }),
  ).toBeVisible();
  await expect(
    page.getByText("Date unknown (today)", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Generate draft" }).click();
  await expect(page.getByLabel("Body")).toHaveValue(
    /Thank you for your message/,
  );
  await page
    .getByLabel("Body")
    .fill("Hello Maya, could you confirm the delivery address?");
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Draft saved." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Request send approval" }).click();
  await expect(
    page.getByText("Hello Maya, could you confirm the delivery address?"),
  ).toBeVisible();
  await expect(page.getByText(/maya.chen@example.invalid/)).toBeVisible();
  await page.getByRole("button", { name: "Approve simulated send" }).click();
  await expect(page.getByText("Simulated action succeeded.")).toBeVisible();

  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Tasks" })
    .click();
  await page.getByRole("button", { name: "Complete task" }).click();
  await expect(page.getByRole("button", { name: "Reopen task" })).toBeVisible();
  await page
    .getByRole("button", { name: "Order #1042 hasn't arrived" })
    .click();
  await page.getByRole("button", { name: "Propose archive" }).click();
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("Rejected", { exact: true })).toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Audit log" })
    .click();
  await expect(page.getByText("Send executed")).toBeVisible();
  await expect(page.getByText("Archive rejected")).toBeVisible();
});

test("mobile inbox and message navigation fit the viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Start demo workspace" }).click();
  await page.getByRole("button", { name: "Open inbox" }).click();
  await page
    .getByRole("button", { name: /Order #1042 hasn't arrived/ })
    .click();
  await expect(
    page.getByRole("heading", { name: "Order #1042 hasn't arrived" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.getByRole("button", { name: "Back to inbox" }).click();
  await expect(
    page.getByRole("button", { name: /Order #1042 hasn't arrived/ }),
  ).toBeVisible();
});

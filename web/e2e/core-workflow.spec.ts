import { expect, test } from "@playwright/test";

test("creates, plans, renders, and regenerates a local mock project", async ({ page }) => {
  const projectName = `Playwright project ${Date.now()}`;

  await page.goto("/projects");
  await page.getByLabel("Project name").fill(projectName);
  await expect(page.getByLabel("Default render profile")).toHaveValue("draft");
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("link", { name: new RegExp(projectName) }).click();
  const projectUrl = page.url();

  await page.getByRole("link", { name: "Characters & voices" }).click();
  await page.getByLabel("Character name").fill("Nova");
  await page.getByRole("button", { name: "Add character" }).click();
  const character = page.getByRole("article").filter({ hasText: "Nova" });
  await expect(character).toBeVisible();
  await character.getByRole("button", { name: "Add mock voice" }).click();
  await expect(character).toContainText("mock-tone-v1");

  await page.goto(projectUrl);
  await page
    .getByLabel("Story text")
    .fill("Nova and Ash enter an abandoned arcade and restore its last glowing machine.");
  await page.getByRole("button", { name: "Save story" }).click();
  await expect(page.getByText("Story saved")).toBeVisible();
  await page.getByRole("button", { name: "Plan mock storyboard" }).click();
  await expect(page.getByText(/Shot 4 ·/)).toBeVisible();

  await page.getByRole("button", { name: "Render with mocks" }).click();
  await expect(page.getByText("Render queued with the draft profile.")).toBeVisible();
  await page.getByRole("link", { name: "Jobs" }).click();
  const renderJob = page.getByRole("article").filter({ hasText: "mock_project_render" });
  await expect(renderJob).toContainText("succeeded");

  await page.getByRole("link", { name: "Renders" }).click();
  const renderCard = page.getByRole("article").filter({ hasText: "draft render" });
  await expect(renderCard).toContainText("854x480 · 24fps · libx264");
  const videoPath = await renderCard.getByRole("link", { name: "Open MP4" }).getAttribute("href");
  expect(videoPath).toBeTruthy();
  const videoResponse = await page.request.get(videoPath!);
  expect(videoResponse.ok()).toBeTruthy();
  expect(videoResponse.headers()["content-type"]).toContain("video/mp4");

  await page.goto(projectUrl);
  await page.getByRole("button", { name: "Regenerate new seed" }).first().click();
  await page.getByRole("link", { name: "Jobs" }).click();
  const regenerationJob = page
    .getByRole("article")
    .filter({ hasText: "mock_shot_regeneration" });
  await expect(regenerationJob).toContainText("succeeded");

  await page.goto(projectUrl);
  await expect(page.getByText("Candidates (2)").first()).toBeVisible();
});

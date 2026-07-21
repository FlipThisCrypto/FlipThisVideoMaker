import { expect, test } from "@playwright/test";

test("creates, plans, renders, and regenerates a local mock project", async ({
  page,
}) => {
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
  await page.getByRole("link", { name: "Continuous video chains" }).click();
  await page.getByLabel("New chain name").fill("E2E continuous chain");
  await page.getByRole("button", { name: "Create video chain" }).click();
  await expect(page.getByRole("heading", { name: "E2E continuous chain" })).toBeVisible();
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
    "base64",
  );
  const frameUploads = page.locator(
    'input[type="file"][accept="image/png,image/jpeg"]',
  );
  await frameUploads.nth(0).setInputFiles({
    name: "start.png",
    mimeType: "image/png",
    buffer: png,
  });
  await frameUploads.nth(1).setInputFiles({
    name: "target.png",
    mimeType: "image/png",
    buffer: png,
  });
  await page
    .getByLabel("Continuous motion and scene prompt")
    .fill("A performer walks naturally while the camera tracks toward the target frame.");
  await expect(page.getByText(/10.000 seconds · constant 60 fps · 600 frames/)).toBeVisible();
  await expect(page.getByLabel("Generation provider")).toHaveValue("wan22-flf-gpu1");
  await expect(page.getByText(/Practical-RIFE runtime or weights are unavailable/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Generate true continuous-motion clip" }),
  ).toBeDisabled();
  await page
    .getByLabel("Speaking-shot eligibility")
    .selectOption("speaking_face_visible");
  await expect(page.getByLabel("Dialogue audio Asset")).toBeVisible();
  await expect(page.getByText(/LatentSync 1.5 runtime or weights are unavailable/)).toBeVisible();

  await page.goto(projectUrl);
  await page
    .getByLabel("Story text")
    .fill(
      "Nova and Ash enter an abandoned arcade and restore its last glowing machine.",
    );
  await page.getByRole("button", { name: "Save story" }).click();
  await expect(page.getByText("Story saved")).toBeVisible();
  await page.getByRole("button", { name: "Plan mock storyboard" }).click();
  await expect(page.getByText(/Shot 4 ·/)).toBeVisible();

  await page.getByLabel("Subtitle output").selectOption("soft");
  await page.getByLabel("Normalize final audio loudness").check();
  await expect(page.getByLabel("Integrated loudness (LUFS)")).toBeEnabled();
  await page.getByRole("button", { name: "Render with mocks" }).click();
  await expect(
    page.getByText(
      "Render queued with the draft profile, soft subtitles, and loudness normalization.",
    ),
  ).toBeVisible();
  await page.getByRole("link", { name: "Jobs" }).click();
  const renderJob = page
    .getByRole("article")
    .filter({ hasText: "mock_project_render" });
  await expect(renderJob).toContainText("succeeded");

  await page.getByRole("link", { name: "Renders" }).click();
  const renderCard = page
    .getByRole("article")
    .filter({ hasText: "draft render" });
  await expect(renderCard).toContainText("854x480 · 24fps · libx264");
  const videoPath = await renderCard
    .getByRole("link", { name: "Open MP4" })
    .getAttribute("href");
  expect(videoPath).toBeTruthy();
  const videoResponse = await page.request.get(videoPath!);
  expect(videoResponse.ok()).toBeTruthy();
  expect(videoResponse.headers()["content-type"]).toContain("video/mp4");

  await page.goto(projectUrl);
  await page
    .getByRole("button", { name: "Regenerate new seed" })
    .first()
    .click();
  await page.getByRole("link", { name: "Jobs" }).click();
  const regenerationJob = page
    .getByRole("article")
    .filter({ hasText: "mock_shot_regeneration" });
  await expect(regenerationJob).toContainText("succeeded");

  await page.goto(projectUrl);
  await expect(page.getByText("Candidates (2)").first()).toBeVisible();
});

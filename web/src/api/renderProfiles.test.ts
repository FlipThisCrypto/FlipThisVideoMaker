import { afterEach, describe, expect, it, vi } from "vitest";

import { enqueueProjectRender, enqueueShotRegeneration } from "./generation";
import { getRenderProfiles } from "./renderProfiles";

afterEach(() => {
  vi.restoreAllMocks();
});

function successfulResponse(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

describe("render profile API", () => {
  it("discovers the configured profiles from the versioned endpoint", async () => {
    const catalog = {
      default_profile: "standard",
      profiles: [
        {
          name: "standard",
          width: 960,
          height: 540,
          fps: 24,
          video_codec: "libx264",
          audio_codec: "aac",
          fallback_profile: "draft",
        },
      ],
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => successfulResponse(catalog));

    await expect(getRenderProfiles()).resolves.toEqual(catalog);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/render-profiles",
      expect.objectContaining({
        headers: expect.objectContaining({
          "Content-Type": "application/json",
        }),
      }),
    );
  });

  it("sends a project render profile in the JSON request body", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() =>
        successfulResponse({ id: "job-1", state: "queued" }),
      );

    await enqueueProjectRender("project-1", "final");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/projects/project-1/render",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ render_profile: "final" }),
      }),
    );
  });

  it("sends the selected profile and seed policy when regenerating a shot", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() =>
        successfulResponse({ id: "job-2", state: "queued" }),
      );

    await enqueueShotRegeneration("shot-1", false, "draft");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/shots/shot-1/regenerate",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          same_seed: false,
          render_profile: "draft",
        }),
      }),
    );
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";

import { createVideoChainClip } from "./videoChains";

function response(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

describe("video-chain API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends Asset IDs and exact native/delivery intent without filesystem paths", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => response({ id: "clip-1", state: "queued" }));
    const request = {
      predecessor_clip_id: "clip-0",
      regenerate_from_predecessor: false,
      provider_id: "luma-ray",
      provider_model: "ray-3.2",
      start_frame_asset_id: "actual-last-asset",
      target_end_frame_asset_id: "target-asset",
      prompt: "Continuous walking and camera motion",
      camera_direction: "slow tracking shot",
      render_profile: "standard",
      gpu_assignment: "gpu1" as const,
      interpolation_mode: "rife" as const,
      interpolation_provider_id: "rife-local" as const,
      audio_reference_asset_id: null,
      lip_sync_mode: "skip" as const,
      lip_sync_provider_id: null,
      lip_sync_settings: {
        eligibility: "explicit_skip" as const,
        speaker_label: null,
        face_index: null,
      },
    };

    await createVideoChainClip("chain-1", request);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/video-chains/chain-1/clips",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(request),
      }),
    );
    const serialized = JSON.stringify(request);
    expect(serialized).not.toContain("file_path");
    expect(serialized).not.toContain("/home/");
    expect(serialized).toContain("actual-last-asset");
    expect(serialized).toContain("rife-local");
  });
});

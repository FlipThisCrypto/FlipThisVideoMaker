import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RenderProfileExecution } from "../types";
import { RenderProfileExecutionSummary } from "./RenderProfileExecutionSummary";

const execution: RenderProfileExecution = {
  version: 1,
  requested_profile: "final",
  effective_profile: "standard",
  profile: {
    width: 1280,
    height: 720,
    fps: 24,
    video_codec: "libx264",
    audio_codec: "aac",
    fallback_profile: "draft",
  },
  fallback_chain: [],
  fallback_history: [
    {
      occurred_at: "2026-07-12T17:00:00Z",
      reason: "provider_out_of_memory",
      provider_id: "local-video",
      operation: "video_generation",
      from_profile: "final",
      to_profile: "standard",
      job_attempt: 1,
      gpu_assignment: "gpu0",
      backend_code: "exit_code:42",
      cleanup_action: "child_process_reaped",
      cleanup_completed: true,
      cleanup_retry_safe: true,
    },
  ],
};

describe("RenderProfileExecutionSummary", () => {
  it("shows requested-to-effective degradation and the safe fallback count", () => {
    const markup = renderToStaticMarkup(
      <RenderProfileExecutionSummary execution={execution} />,
    );

    expect(markup).toContain("final → standard after 1 safe fallback");
  });
});

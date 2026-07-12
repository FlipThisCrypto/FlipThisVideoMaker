import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RenderProfile } from "../types";
import { RenderProfileSelect } from "./RenderProfileSelect";

const profile: RenderProfile = {
  name: "standard",
  width: 960,
  height: 540,
  fps: 24,
  video_codec: "libx264",
  audio_codec: "aac",
  fallback_profile: "draft",
};

describe("RenderProfileSelect", () => {
  it("associates its visible label and profile details with the native select", () => {
    const markup = renderToStaticMarkup(
      <RenderProfileSelect
        label="Profile for this render"
        profiles={[profile]}
        value="standard"
        onChange={() => undefined}
      />,
    );

    const labelTarget = markup.match(/<label[^>]*for="([^"]+)"/)?.[1];
    expect(labelTarget).toBeTruthy();
    expect(markup).toContain(`id="${labelTarget}"`);
    expect(markup).toContain("Profile for this render");
    expect(markup).toContain("960×540 at 24fps");
    expect(markup).toContain("video libx264 · audio aac");
    expect(markup).toContain("configured fallback: draft");
    expect(markup).toContain("aria-describedby=");
  });

  it("keeps a stale persisted value visible without claiming it is configured", () => {
    const markup = renderToStaticMarkup(
      <RenderProfileSelect
        label="Default render profile"
        profiles={[profile]}
        value="retired"
        onChange={() => undefined}
      />,
    );

    expect(markup).toContain("retired (not currently configured)");
  });
});

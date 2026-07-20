import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Asset } from "../types";
import {
  createDefaultMusic,
  createDefaultRenderFinalization,
} from "../api/renderFinalization";
import { RenderFinalizationControls } from "./RenderFinalizationControls";

const audioAsset: Asset = {
  id: "music-asset",
  project_id: "project-1",
  shot_id: null,
  type: "upload",
  mime_type: "audio/wav",
  checksum: "a".repeat(64),
  width: null,
  height: null,
  duration: 12.34,
};

describe("RenderFinalizationControls", () => {
  it("renders labelled native controls with safe defaults", () => {
    const markup = renderToStaticMarkup(
      <RenderFinalizationControls
        value={createDefaultRenderFinalization()}
        audioAssets={[audioAsset]}
        onChange={() => undefined}
        onUpload={() => undefined}
      />,
    );

    expect(markup).toContain("<legend");
    expect(markup).toContain("Final media");
    expect(markup).toContain("Sidecar SRT only");
    expect(markup).toContain("Normalize final audio loudness");
    expect(markup).toContain("Background music asset");
    expect(markup).toContain("upload · aaaaaaaa · 12.3s");
    expect(markup).not.toContain("file_path");
    expect(markup).not.toContain("/home/");

    for (const text of [
      "Subtitle output",
      "Subtitle language",
      "Subtitle track title",
      "Background music asset",
      "Upload WAV or MP3",
    ]) {
      const label = markup.match(
        new RegExp(`<label[^>]*for="([^"]+)"[^>]*>[^<]*(?:<[^>]+>)*${text}`),
      );
      expect(label?.[1], `${text} should have a labelled control`).toBeTruthy();
      expect(markup).toContain(`id="${label?.[1]}"`);
    }
  });

  it("shows music and automatic ducking settings only for a selected asset", () => {
    const value = createDefaultRenderFinalization();
    value.subtitle.mode = "soft";
    value.audio.normalize = true;
    value.music = createDefaultMusic(audioAsset.id);
    const markup = renderToStaticMarkup(
      <RenderFinalizationControls
        value={value}
        audioAssets={[audioAsset]}
        onChange={() => undefined}
        onUpload={() => undefined}
        uploadSucceeded
      />,
    );

    expect(markup).toContain("Music uploaded and selected.");
    expect(markup).toContain(
      "Music is automatically ducked beneath programme audio.",
    );
    expect(markup).toContain("Advanced ducking controls");
    expect(markup).toContain("Music gain (dB)");
    expect(markup).toContain('value="music-asset" selected=""');
  });
});

import type {
  MusicFinalizationSettings,
  RenderFinalizationRequest,
} from "../types";

export function createDefaultRenderFinalization(): RenderFinalizationRequest {
  return {
    version: 1,
    subtitle: {
      mode: "sidecar",
      language: "eng",
      title: "Subtitles",
      default: true,
      forced: false,
    },
    audio: {
      normalize: false,
      integrated_lufs: -16,
      loudness_range_lu: 11,
      true_peak_dbfs: -1.5,
    },
    music: null,
  };
}

export function createDefaultMusic(assetId: string): MusicFinalizationSettings {
  return {
    asset_id: assetId,
    gain_db: -18,
    loop: true,
    threshold: 0.03,
    ratio: 8,
    attack_ms: 20,
    release_ms: 300,
  };
}

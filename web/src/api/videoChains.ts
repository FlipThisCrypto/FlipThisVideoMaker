import { api } from "./client";
import type { Asset, Job, VideoChain, VideoChainClip } from "../types";

export interface CreateVideoChainRequest {
  name: string;
  description: string;
  continuation_mode: string;
  buffer_target_seconds: number;
}

export interface CreateVideoChainClipRequest {
  predecessor_clip_id: string | null;
  regenerate_from_predecessor: boolean;
  provider_id: string;
  provider_model: string;
  start_frame_asset_id: string;
  target_end_frame_asset_id: string;
  prompt: string;
  camera_direction: string;
  render_profile: string;
  gpu_assignment: "gpu0" | "gpu1";
  interpolation_mode: "rife";
  interpolation_provider_id: "rife-local";
  audio_reference_asset_id: string | null;
  lip_sync_mode: "skip" | "latentsync";
  lip_sync_provider_id: "latentsync-local" | null;
  lip_sync_settings: {
    eligibility:
      | "speaking_face_visible"
      | "narration_no_visible_speaker"
      | "mouth_hidden"
      | "multiple_faces"
      | "no_speech"
      | "explicit_skip";
    speaker_label: string | null;
    face_index: number | null;
  };
}

export function createVideoChain(
  projectId: string,
  request: CreateVideoChainRequest,
) {
  return api<VideoChain>(`/projects/${projectId}/video-chains`, {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export function createVideoChainClip(
  chainId: string,
  request: CreateVideoChainClipRequest,
) {
  return api<VideoChainClip>(`/video-chains/${chainId}/clips`, {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export function reviewVideoChainClip(
  clipId: string,
  operation: "accept" | "reject",
) {
  return api<VideoChainClip>(`/video-chain-clips/${clipId}/${operation}`, {
    method: "POST",
  });
}

export function retryVideoChainClip(
  clipId: string,
  acknowledgeOrphanRisk = false,
) {
  const query = acknowledgeOrphanRisk ? "?acknowledge_orphan_risk=true" : "";
  return api<Job>(`/video-chain-clips/${clipId}/retry${query}`, {
    method: "POST",
  });
}

export function assembleVideoChain(chainId: string) {
  return api<Asset>(`/video-chains/${chainId}/assemble`, { method: "POST" });
}

export function publishVideoChainStream(chainId: string) {
  return api<Asset>(`/video-chains/${chainId}/stream/publish`, {
    method: "POST",
  });
}

export function changeVideoChainState(
  chainId: string,
  operation: "pause" | "resume" | "cancel",
) {
  return api<VideoChain>(`/video-chains/${chainId}/${operation}`, {
    method: "POST",
  });
}

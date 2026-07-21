export interface Project {
  id: string;
  name: string;
  description: string;
  target_duration: number;
  aspect_ratio: string;
  resolution_profile: string;
  fps: number;
  status: string;
  root_asset_directory: string;
  original_story: string;
  global_visual_style: string;
  global_negative_prompt: string;
  created_at: string;
  updated_at: string;
}
export interface RenderProfile {
  name: string;
  width: number;
  height: number;
  fps: number;
  video_codec: string;
  audio_codec: string;
  fallback_profile: string | null;
}
export interface RenderProfileCatalog {
  default_profile: string;
  profiles: RenderProfile[];
}
export type SubtitleMode = "sidecar" | "soft" | "burned";
export interface SubtitleFinalizationSettings {
  mode: SubtitleMode;
  language: string;
  title: string;
  default: boolean;
  forced: boolean;
}
export interface AudioFinalizationSettings {
  normalize: boolean;
  integrated_lufs: number;
  loudness_range_lu: number;
  true_peak_dbfs: number;
}
export interface MusicFinalizationSettings {
  asset_id: string;
  gain_db: number;
  loop: boolean;
  threshold: number;
  ratio: number;
  attack_ms: number;
  release_ms: number;
}
export interface RenderFinalizationRequest {
  version: 1;
  subtitle: SubtitleFinalizationSettings;
  audio: AudioFinalizationSettings;
  music: MusicFinalizationSettings | null;
}
export interface ProjectRenderRequest {
  render_profile: string;
  finalization: RenderFinalizationRequest;
}
export interface RenderProfileExecution {
  version: 1;
  requested_profile: string;
  effective_profile: string;
  profile: Omit<RenderProfile, "name">;
  fallback_chain: Array<{
    name: string;
    profile: Omit<RenderProfile, "name">;
  }>;
  fallback_history: Array<{
    occurred_at: string;
    reason: "provider_out_of_memory";
    provider_id: string;
    operation: string;
    from_profile: string;
    to_profile: string;
    job_attempt: number;
    gpu_assignment: string;
    backend_code: string | null;
    cleanup_action: string;
    cleanup_completed: boolean;
    cleanup_retry_safe: boolean;
  }>;
}
export interface Shot {
  id: string;
  scene_id: string;
  sequence_number: number;
  shot_type: string;
  duration: number;
  prompt: string;
  negative_prompt: string;
  dialogue: string;
  narration: string;
  speaker: string | null;
  camera: Record<string, string>;
  character_positions: Record<string, unknown>;
  character_actions: Record<string, unknown>;
  status: string;
  provider: string;
  model: string;
  seed: number;
  transition_type: string;
  overlap_frame_count: number;
  planned_start_frame_id: string | null;
  planned_end_frame_id: string | null;
  actual_start_frame_id: string | null;
  actual_end_frame_id: string | null;
  continuity_source_frame_id: string | null;
  continuity_target_frame_id: string | null;
  selected_candidate_id: string | null;
  continuity_packet: Record<string, unknown>;
  generation_settings: Record<string, unknown>;
  approval_state: string;
  retry_count: number;
}
export interface Job {
  id: string;
  job_type: string;
  project_id: string;
  shot_id: string | null;
  provider: string;
  gpu_assignment: string;
  state: string;
  progress: number;
  current_stage: string;
  attempt_number: number;
  claimed_by_worker_id: string | null;
  claimed_by_instance_id: string | null;
  lease_heartbeat_at: string | null;
  lease_expires_at: string | null;
  error_info: Record<string, unknown>;
  log_path: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  render_profile_execution: RenderProfileExecution | null;
  render_profile_execution_error: string | null;
  first_last_frame_generation: FirstLastFrameGenerationRequest | null;
  first_last_frame_generation_error: string | null;
}
export interface WorkerStatus {
  id: string;
  assignment: string;
  configured: boolean;
  configured_max_concurrent_jobs: number | null;
  physical_gpu: number | null;
  runtime_state: string | null;
  online: boolean;
  instance_id: string | null;
  hostname: string | null;
  pid: number | null;
  current_job_id: string | null;
  started_at: string | null;
  last_heartbeat_at: string | null;
  stopped_at: string | null;
}
export interface Render {
  id: string;
  project_id: string;
  render_profile: string;
  output_path: string;
  codec: string;
  resolution: string;
  frame_rate: number;
  audio_configuration: Record<string, unknown>;
  subtitle_configuration: Record<string, unknown>;
  creation_metadata: Record<string, unknown>;
  created_at: string;
}
export interface Character {
  id: string;
  project_id: string;
  name: string;
  description: string;
  canonical_appearance: string;
  personality_notes: string;
  wardrobe_rules: string;
  color_palette: string[];
  reference_images: string[];
  expression_references: string[];
  pose_references: string[];
  negative_identity_traits: string;
  model_references: Record<string, unknown>;
  consent_provenance: Record<string, unknown>;
  default_voice_profile_id: string | null;
}
export interface VoiceProfile {
  id: string;
  character_id: string;
  provider: string;
  model: string;
  reference_audio: string | null;
  language: string;
  speaking_style: string;
  speed: number;
  pitch: number;
  emotion_defaults: Record<string, unknown>;
  consent_acknowledged: boolean;
}
export interface Scene {
  id: string;
  project_id: string;
  number: number;
  title: string;
  location: string;
  time_of_day: string;
  lighting: string;
  characters: string[];
  props: string[];
  environment: string;
  continuity_state: Record<string, unknown>;
}
export interface Asset {
  id: string;
  project_id: string;
  shot_id: string | null;
  type: string;
  mime_type: string;
  checksum: string;
  width: number | null;
  height: number | null;
  duration: number | null;
  frame_rate?: number | null;
  source_provider?: string;
  generation_parameters?: Record<string, unknown>;
  created_at?: string;
}

export type GenerationCategory =
  | "mock_test_video"
  | "still_image_animation"
  | "frame_interpolation"
  | "first_frame_image_to_video"
  | "first_last_frame_generative_video"
  | "performance_conditioned_video"
  | "frame_rate_conversion";

export interface ProviderInfo {
  id: string;
  name: string;
  model_identity: string;
  capabilities: string[];
  available: boolean;
  supported_inputs: string[];
  max_duration_seconds: number | null;
  max_width: number | null;
  max_height: number | null;
  native_frame_rates: number[];
  supported_durations_seconds: number[];
  generation_category: GenerationCategory | null;
  cancellation_supported: boolean;
  progress_supported: boolean;
  notes: string;
}

export interface ProviderHealth {
  provider: string;
  ok: boolean;
  status?: string;
  api_version?: string | null;
  [key: string]: unknown;
}

export interface FirstLastFrameGenerationRequest {
  version: 1;
  generation_category: "first_last_frame_generative_video";
  provider_id: string;
  provider_model: string;
  provider_version: string | null;
  start_frame_asset_id: string;
  target_end_frame_asset_id: string;
  prompt: string;
  negative_prompt: string;
  duration_seconds: number;
  native_requested_fps: number;
  delivery_fps: number;
  width: number;
  height: number;
  aspect_ratio: string;
  seed: number | null;
  motion_strength: number | null;
  camera_direction: string;
  identity_reference_asset_ids: string[];
  audio_reference_asset_id: string | null;
  lip_sync_mode: string;
  lip_sync_provider_id: string | null;
  lip_sync_settings: {
    eligibility: string;
    speaker_label: string | null;
    face_index: number | null;
  };
  interpolation_mode: string;
  interpolation_provider_id: string | null;
  safety: Record<string, unknown>;
  provider_settings: Record<string, Record<string, unknown>>;
  captured_render_profile: Record<string, unknown>;
  captured_fallback_policy: Record<string, unknown>;
  retry_continuation: {
    attempt: number;
    continuation_mode: string;
    predecessor_clip_id: string | null;
    retry_of_job_id: string | null;
  };
}

export interface VideoChain {
  id: string;
  project_id: string;
  name: string;
  description: string;
  continuation_mode: string;
  state: string;
  active_lineage_version: number;
  buffer_target_seconds: number;
  playlist_asset_id: string | null;
  assembled_asset_id: string | null;
  stream_state: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface VideoChainClip {
  id: string;
  chain_id: string;
  sequence_number: number;
  revision: number;
  lineage_version: number;
  predecessor_clip_id: string | null;
  planned_start_frame_asset_id: string;
  target_end_frame_asset_id: string;
  actual_start_frame_asset_id: string | null;
  actual_last_frame_asset_id: string | null;
  native_video_asset_id: string | null;
  delivery_video_asset_id: string | null;
  qa_report_asset_id: string | null;
  job_id: string | null;
  state: string;
  request_snapshot: FirstLastFrameGenerationRequest;
  request_digest: string;
  result_snapshot: Record<string, unknown>;
  provider_job_id: string | null;
  provider_warnings: string[];
  failure_info: Record<string, unknown>;
  accepted_at: string | null;
  rejected_at: string | null;
  created_at: string;
  updated_at: string;
}
export interface Candidate {
  id: string;
  shot_id: string;
  provider: string;
  model: string;
  prompt: string;
  negative_prompt: string;
  seed: number;
  settings: Record<string, unknown>;
  generation_seconds: number;
  gpu: string;
  input_asset_ids: string[];
  output_asset_id: string | null;
  first_frame_asset_id: string | null;
  last_frame_asset_id: string | null;
  qa_results: Record<string, unknown>;
  user_rating: number | null;
  disposition: string;
  created_at: string;
}

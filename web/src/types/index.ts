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
  created_at: string;
  updated_at: string;
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
  speaker: string | null;
  camera: Record<string, string>;
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
  error_info: Record<string, unknown>;
  log_path: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
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

import { useId } from "react";

import type {
  Asset,
  MusicFinalizationSettings,
  RenderFinalizationRequest,
  SubtitleMode,
} from "../types";
import { createDefaultMusic } from "../api/renderFinalization";

interface RenderFinalizationControlsProps {
  value: RenderFinalizationRequest;
  audioAssets: Asset[];
  onChange: (value: RenderFinalizationRequest) => void;
  onUpload: (file: File) => void;
  disabled?: boolean;
  uploadPending?: boolean;
  uploadError?: string;
  uploadSucceeded?: boolean;
}

function assetLabel(asset: Asset) {
  const duration =
    asset.duration === null
      ? "duration unknown"
      : `${asset.duration.toFixed(1)}s`;
  return `${asset.type} · ${asset.checksum.slice(0, 8)} · ${duration}`;
}

export function RenderFinalizationControls({
  value,
  audioAssets,
  onChange,
  onUpload,
  disabled = false,
  uploadPending = false,
  uploadError = "",
  uploadSucceeded = false,
}: RenderFinalizationControlsProps) {
  const id = useId();
  const subtitleMetadataDisabled =
    disabled || value.subtitle.mode === "sidecar";
  const audioTargetDisabled = disabled || !value.audio.normalize;
  const musicDisabled = disabled || value.music === null;

  function setSubtitle(patch: Partial<RenderFinalizationRequest["subtitle"]>) {
    onChange({
      ...value,
      subtitle: { ...value.subtitle, ...patch },
    });
  }

  function setAudio(patch: Partial<RenderFinalizationRequest["audio"]>) {
    onChange({
      ...value,
      audio: { ...value.audio, ...patch },
    });
  }

  function setMusic(patch: Partial<MusicFinalizationSettings>) {
    if (value.music === null) return;
    onChange({
      ...value,
      music: { ...value.music, ...patch },
    });
  }

  function selectMusic(assetId: string) {
    if (!assetId) {
      onChange({ ...value, music: null });
      return;
    }
    onChange({
      ...value,
      audio: { ...value.audio, normalize: true },
      music: createDefaultMusic(assetId),
    });
  }

  return (
    <fieldset
      className="mt-4 border-t border-slate-700 pt-4"
      disabled={disabled}
    >
      <legend className="px-1 font-bold">Final media</legend>
      <div className="grid gap-3 md:grid-cols-2">
        <label htmlFor={`${id}-subtitle-mode`}>
          <span className="text-sm">Subtitle output</span>
          <select
            id={`${id}-subtitle-mode`}
            className="field"
            value={value.subtitle.mode}
            onChange={(event) =>
              setSubtitle({ mode: event.target.value as SubtitleMode })
            }
          >
            <option value="sidecar">Sidecar SRT only</option>
            <option value="soft">Selectable subtitle track</option>
            <option value="burned">Burn subtitles into video</option>
          </select>
        </label>
        <label htmlFor={`${id}-subtitle-language`}>
          <span className="text-sm">Subtitle language</span>
          <input
            id={`${id}-subtitle-language`}
            className="field"
            value={value.subtitle.language}
            disabled={subtitleMetadataDisabled}
            onChange={(event) => setSubtitle({ language: event.target.value })}
          />
        </label>
        <label className="md:col-span-2" htmlFor={`${id}-subtitle-title`}>
          <span className="text-sm">Subtitle track title</span>
          <input
            id={`${id}-subtitle-title`}
            className="field"
            value={value.subtitle.title}
            disabled={subtitleMetadataDisabled}
            onChange={(event) => setSubtitle({ title: event.target.value })}
          />
        </label>
        <label
          className="flex items-center gap-2 text-sm"
          htmlFor={`${id}-subtitle-default`}
        >
          <input
            id={`${id}-subtitle-default`}
            type="checkbox"
            checked={value.subtitle.default}
            disabled={subtitleMetadataDisabled}
            onChange={(event) => setSubtitle({ default: event.target.checked })}
          />
          Make the selectable subtitle track the default
        </label>
        <label
          className="flex items-center gap-2 text-sm"
          htmlFor={`${id}-subtitle-forced`}
        >
          <input
            id={`${id}-subtitle-forced`}
            type="checkbox"
            checked={value.subtitle.forced}
            disabled={subtitleMetadataDisabled}
            onChange={(event) => setSubtitle({ forced: event.target.checked })}
          />
          Mark the selectable subtitle track as forced
        </label>
      </div>

      <div className="mt-4 border-t border-slate-700 pt-4">
        <label
          className="flex items-center gap-2 text-sm"
          htmlFor={`${id}-normalize`}
        >
          <input
            id={`${id}-normalize`}
            type="checkbox"
            checked={value.audio.normalize}
            disabled={value.music !== null}
            onChange={(event) => setAudio({ normalize: event.target.checked })}
          />
          Normalize final audio loudness
        </label>
        {value.music !== null && (
          <p className="mt-1 text-xs text-slate-400">
            Normalization stays enabled while background music is selected.
          </p>
        )}
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <NumberControl
            id={`${id}-integrated-lufs`}
            label="Integrated loudness (LUFS)"
            value={value.audio.integrated_lufs}
            min={-70}
            max={-5}
            step={0.1}
            disabled={audioTargetDisabled}
            onChange={(integrated_lufs) => setAudio({ integrated_lufs })}
          />
          <NumberControl
            id={`${id}-loudness-range`}
            label="Loudness range (LU)"
            value={value.audio.loudness_range_lu}
            min={1}
            max={50}
            step={0.1}
            disabled={audioTargetDisabled}
            onChange={(loudness_range_lu) => setAudio({ loudness_range_lu })}
          />
          <NumberControl
            id={`${id}-true-peak`}
            label="True peak (dBFS)"
            value={value.audio.true_peak_dbfs}
            min={-9}
            max={0}
            step={0.1}
            disabled={audioTargetDisabled}
            onChange={(true_peak_dbfs) => setAudio({ true_peak_dbfs })}
          />
        </div>
      </div>

      <div className="mt-4 border-t border-slate-700 pt-4">
        <label htmlFor={`${id}-music-asset`}>
          <span className="text-sm">Background music asset</span>
          <select
            id={`${id}-music-asset`}
            className="field"
            value={value.music?.asset_id ?? ""}
            onChange={(event) => selectMusic(event.target.value)}
          >
            <option value="">No background music</option>
            {audioAssets.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {assetLabel(asset)}
              </option>
            ))}
          </select>
        </label>
        <label
          className={`button mt-2 inline-block ${uploadPending ? "cursor-wait" : "cursor-pointer"}`}
          htmlFor={`${id}-music-upload`}
        >
          {uploadPending ? "Uploading music…" : "Upload WAV or MP3"}
        </label>
        <input
          id={`${id}-music-upload`}
          className="sr-only"
          type="file"
          accept="audio/wav,audio/mpeg,.wav,.mp3"
          disabled={disabled || uploadPending}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onUpload(file);
            event.currentTarget.value = "";
          }}
        />
        {uploadSucceeded && (
          <p className="mt-2 text-sm text-teal-200" aria-live="polite">
            Music uploaded and selected.
          </p>
        )}
        {uploadError && (
          <p className="mt-2 text-sm text-red-300" role="alert">
            Music upload failed: {uploadError}
          </p>
        )}
        <p className="mt-2 text-xs text-slate-400">
          Selecting music enables normalization. Music is automatically ducked
          beneath programme audio.
        </p>
        {value.music !== null && (
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <NumberControl
              id={`${id}-music-gain`}
              label="Music gain (dB)"
              value={value.music.gain_db}
              min={-60}
              max={12}
              step={0.1}
              disabled={musicDisabled}
              onChange={(gain_db) => setMusic({ gain_db })}
            />
            <label
              className="flex items-center gap-2 self-end pb-2 text-sm"
              htmlFor={`${id}-loop`}
            >
              <input
                id={`${id}-loop`}
                type="checkbox"
                checked={value.music.loop}
                disabled={musicDisabled}
                onChange={(event) => setMusic({ loop: event.target.checked })}
              />
              Loop music to the video duration
            </label>
            <details className="md:col-span-2">
              <summary>Advanced ducking controls</summary>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <NumberControl
                  id={`${id}-duck-threshold`}
                  label="Ducking threshold"
                  value={value.music.threshold}
                  min={0.00097563}
                  max={1}
                  step={0.001}
                  disabled={musicDisabled}
                  onChange={(threshold) => setMusic({ threshold })}
                />
                <NumberControl
                  id={`${id}-duck-ratio`}
                  label="Ducking ratio"
                  value={value.music.ratio}
                  min={1}
                  max={20}
                  step={0.1}
                  disabled={musicDisabled}
                  onChange={(ratio) => setMusic({ ratio })}
                />
                <NumberControl
                  id={`${id}-duck-attack`}
                  label="Ducking attack (ms)"
                  value={value.music.attack_ms}
                  min={0.01}
                  max={2000}
                  step={0.01}
                  disabled={musicDisabled}
                  onChange={(attack_ms) => setMusic({ attack_ms })}
                />
                <NumberControl
                  id={`${id}-duck-release`}
                  label="Ducking release (ms)"
                  value={value.music.release_ms}
                  min={0.01}
                  max={9000}
                  step={0.01}
                  disabled={musicDisabled}
                  onChange={(release_ms) => setMusic({ release_ms })}
                />
              </div>
            </details>
          </div>
        )}
      </div>
    </fieldset>
  );
}

function NumberControl({
  id,
  label,
  value,
  min,
  max,
  step,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  disabled: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <label htmlFor={id}>
      <span className="text-sm">{label}</span>
      <input
        id={id}
        className="field"
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

import { useId } from "react";

import type { RenderProfile } from "../types";

interface RenderProfileSelectProps {
  label: string;
  profiles: RenderProfile[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
}

export function RenderProfileSelect({
  label,
  profiles,
  value,
  onChange,
  disabled = false,
  className = "",
}: RenderProfileSelectProps) {
  const inputId = useId();
  const descriptionId = `${inputId}-description`;
  const selected = profiles.find((profile) => profile.name === value);
  const isUnknownValue = Boolean(value) && profiles.length > 0 && !selected;
  const isCatalogPending = Boolean(value) && profiles.length === 0;

  return (
    <label className={className} htmlFor={inputId}>
      <span className="text-sm">{label}</span>
      <select
        id={inputId}
        className="field"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        aria-describedby={selected ? descriptionId : undefined}
      >
        {!value && <option value="">Select a render profile</option>}
        {isCatalogPending && <option value={value}>{value}</option>}
        {isUnknownValue && (
          <option value={value}>{value} (not currently configured)</option>
        )}
        {profiles.map((profile) => (
          <option key={profile.name} value={profile.name}>
            {profile.name} — {profile.width}×{profile.height} at {profile.fps}
            fps
          </option>
        ))}
      </select>
      {selected && (
        <span id={descriptionId} className="mt-1 block text-xs text-slate-400">
          {selected.width}×{selected.height} · {selected.fps}fps · video{" "}
          {selected.video_codec} · audio {selected.audio_codec} · configured
          fallback: {selected.fallback_profile ?? "none"}
        </span>
      )}
    </label>
  );
}

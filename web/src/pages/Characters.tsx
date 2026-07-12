import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { api } from "../api/client";
import type { Asset, Character, VoiceProfile } from "../types";

export function Characters() {
  const { id: projectId } = useParams();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const { data: characters = [] } = useQuery({
    queryKey: ["characters", projectId],
    queryFn: () => api<Character[]>(`/projects/${projectId}/characters`),
  });
  const create = useMutation({
    mutationFn: () =>
      api<Character>(`/projects/${projectId}/characters`, {
        method: "POST",
        body: JSON.stringify({
          name,
          consent_provenance: { kind: "fictional", recorded_in: "local_ui" },
        }),
      }),
    onSuccess: () => {
      setName("");
      void client.invalidateQueries({ queryKey: ["characters", projectId] });
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    if (name.trim()) create.mutate();
  }
  return (
    <>
      <h1 className="mb-6 text-3xl font-bold">Character library</h1>
      <form className="card mb-6 flex gap-3" onSubmit={submit}>
        <label className="flex-1">
          <span className="mb-1 block text-sm">Character name</span>
          <input
            className="field"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <button className="button self-end" disabled={create.isPending}>
          Add character
        </button>
      </form>
      <div className="space-y-5">
        {characters.map((character) => (
          <CharacterCard key={character.id} character={character} />
        ))}
      </div>
    </>
  );
}

function CharacterCard({ character }: { character: Character }) {
  const client = useQueryClient();
  const [appearance, setAppearance] = useState(character.canonical_appearance);
  const [wardrobe, setWardrobe] = useState(character.wardrobe_rules);
  const [preview, setPreview] = useState<Asset | null>(null);
  const { data: voices = [] } = useQuery({
    queryKey: ["voices", character.id],
    queryFn: () =>
      api<VoiceProfile[]>(`/characters/${character.id}/voice-profiles`),
  });
  const save = useMutation({
    mutationFn: () =>
      api(`/characters/${character.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          canonical_appearance: appearance,
          wardrobe_rules: wardrobe,
        }),
      }),
    onSuccess: () =>
      void client.invalidateQueries({
        queryKey: ["characters", character.project_id],
      }),
  });
  const createVoice = useMutation({
    mutationFn: () =>
      api<VoiceProfile>(`/characters/${character.id}/voice-profiles`, {
        method: "POST",
        body: JSON.stringify({
          provider: "mock",
          consent_acknowledged: true,
        }),
      }),
    onSuccess: (voice) => {
      void api(`/characters/${character.id}`, {
        method: "PATCH",
        body: JSON.stringify({ default_voice_profile_id: voice.id }),
      });
      void client.invalidateQueries({ queryKey: ["voices", character.id] });
    },
  });
  const removeCharacter = useMutation({
    mutationFn: () =>
      api(`/characters/${character.id}`, {
        method: "DELETE",
      }),
    onSuccess: () =>
      void client.invalidateQueries({
        queryKey: ["characters", character.project_id],
      }),
  });
  async function uploadReference(file: File | undefined) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    await api(`/characters/${character.id}/references`, {
      method: "POST",
      body: form,
    });
    await client.invalidateQueries({
      queryKey: ["characters", character.project_id],
    });
  }
  async function previewVoice(voice: VoiceProfile) {
    const asset = await api<Asset>(
      `/voice-profiles/${voice.id}/preview?text=Voice%20preview`,
      { method: "POST" },
    );
    setPreview(asset);
  }
  return (
    <article className="card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-bold">{character.name}</h2>
        <div className="flex flex-wrap gap-2">
          <label className="button cursor-pointer">
            Add reference image
            <input
              className="sr-only"
              type="file"
              accept="image/png,image/jpeg"
              onChange={(event) => void uploadReference(event.target.files?.[0])}
            />
          </label>
          <button
            className="button"
            aria-label={`Delete character ${character.name}`}
            disabled={removeCharacter.isPending}
            onClick={() => {
              if (window.confirm(`Delete character ${character.name}?`)) {
                removeCharacter.mutate();
              }
            }}
          >
            Delete character
          </button>
        </div>
      </div>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <label>
          <span className="mb-1 block text-sm">Canonical appearance</span>
          <textarea
            className="field"
            value={appearance}
            onChange={(event) => setAppearance(event.target.value)}
          />
        </label>
        <label>
          <span className="mb-1 block text-sm">Wardrobe rules</span>
          <textarea
            className="field"
            value={wardrobe}
            onChange={(event) => setWardrobe(event.target.value)}
          />
        </label>
      </div>
      <button
        className="button mt-3"
        onClick={() => save.mutate()}
        disabled={save.isPending}
      >
        Save character
      </button>
      <div className="mt-4 flex flex-wrap gap-2">
        {character.reference_images.map((assetId) => (
          <img
            key={assetId}
            className="h-28 w-28 rounded object-cover"
            src={`/api/v1/assets/${assetId}/file`}
            alt={`${character.name} reference`}
          />
        ))}
      </div>
      <div className="mt-5 border-t border-slate-700 pt-4">
        <div className="flex items-center justify-between">
          <h3 className="font-bold">Voice profiles</h3>
          <button className="button" onClick={() => createVoice.mutate()}>
            Add mock voice
          </button>
        </div>
        {voices.map((voice) => (
          <VoiceRow
            key={voice.id}
            voice={voice}
            characterName={character.name}
            onPreview={() => void previewVoice(voice)}
          />
        ))}
        {preview && (
          <audio
            className="mt-3"
            controls
            src={`/api/v1/assets/${preview.id}/file`}
          >
            <track kind="captions" />
          </audio>
        )}
      </div>
    </article>
  );
}

function VoiceRow({
  voice,
  characterName,
  onPreview,
}: {
  voice: VoiceProfile;
  characterName: string;
  onPreview: () => void;
}) {
  const client = useQueryClient();
  const [consent, setConsent] = useState(false);
  const [uploadError, setUploadError] = useState("");

  async function uploadReference(file: File | undefined) {
    if (!file || !consent) return;
    const form = new FormData();
    form.append("file", file);
    try {
      setUploadError("");
      await api(
        `/voice-profiles/${voice.id}/reference-audio?consent_acknowledged=true`,
        { method: "POST", body: form },
      );
      await client.invalidateQueries({ queryKey: ["voices", voice.character_id] });
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Upload failed");
    }
  }

  const remove = useMutation({
    mutationFn: () => api(`/voice-profiles/${voice.id}`, { method: "DELETE" }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["voices", voice.character_id] }),
  });

  return (
    <div className="mt-3 rounded border border-slate-700 p-3">
      <div className="flex flex-wrap items-center gap-3">
        <span>
          {voice.provider} · {voice.model} · {voice.language}
        </span>
        <button className="button" onClick={onPreview}>
          Preview
        </button>
        <button
          className="button"
          aria-label={`Delete voice for ${characterName}`}
          disabled={remove.isPending}
          onClick={() => {
            if (window.confirm(`Delete this voice profile for ${characterName}?`)) {
              remove.mutate();
            }
          }}
        >
          Delete voice
        </button>
      </div>
      <label className="mt-3 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={consent}
          onChange={(event) => setConsent(event.target.checked)}
        />
        I confirm I have consent to use this voice reference.
      </label>
      <label
        className={`button mt-2 inline-block ${consent ? "cursor-pointer" : "cursor-not-allowed opacity-50"}`}
      >
        Add voice reference audio
        <input
          className="sr-only"
          type="file"
          accept="audio/wav,audio/mpeg"
          disabled={!consent}
          onChange={(event) => void uploadReference(event.target.files?.[0])}
        />
      </label>
      {voice.reference_audio && (
        <p className="mt-2 text-sm text-teal-200">Voice reference recorded.</p>
      )}
      {uploadError && (
        <p className="mt-2 text-sm text-red-300" role="alert">
          Voice reference upload failed: {uploadError}
        </p>
      )}
    </div>
  );
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import {
  enqueueProjectRender,
  enqueueShotRegeneration,
} from "../api/generation";
import {
  createDefaultMusic,
  createDefaultRenderFinalization,
} from "../api/renderFinalization";
import { useRenderProfiles } from "../api/renderProfiles";
import { RenderFinalizationControls } from "../components/RenderFinalizationControls";
import { RenderProfileSelect } from "../components/RenderProfileSelect";
import type {
  Asset,
  Candidate,
  Project,
  ProjectRenderRequest,
  RenderFinalizationRequest,
  RenderProfile,
  Scene,
  Shot,
} from "../types";
export function ProjectEditor() {
  const { id } = useParams();
  const client = useQueryClient();
  const [story, setStory] = useState("");
  const [projectProfile, setProjectProfile] = useState("");
  const [renderProfile, setRenderProfile] = useState("");
  const [finalization, setFinalization] = useState<RenderFinalizationRequest>(
    createDefaultRenderFinalization,
  );
  const profileCatalog = useRenderProfiles();
  const { data: project } = useQuery({
    queryKey: ["project", id],
    queryFn: () => api<Project>(`/projects/${id}`),
  });
  const { data: shots = [] } = useQuery({
    queryKey: ["shots", id],
    queryFn: () => api<Shot[]>(`/projects/${id}/shots`),
  });
  const { data: scenes = [] } = useQuery({
    queryKey: ["scenes", id],
    queryFn: () => api<Scene[]>(`/projects/${id}/scenes`),
  });
  const assetsQuery = useQuery({
    queryKey: ["assets", id],
    queryFn: () => api<Asset[]>(`/projects/${id}/assets`),
  });
  const assets = assetsQuery.data ?? [];
  const render = useMutation({
    mutationFn: (request: ProjectRenderRequest) =>
      enqueueProjectRender(id!, request),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["jobs"] }),
  });
  const uploadMusic = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api<Asset>(`/projects/${id}/assets`, {
        method: "POST",
        body: form,
      });
    },
    onSuccess: (asset) => {
      client.setQueryData<Asset[]>(["assets", id], (current = []) => [
        asset,
        ...current.filter((item) => item.id !== asset.id),
      ]);
      setFinalization((current) => ({
        ...current,
        audio: { ...current.audio, normalize: true },
        music: createDefaultMusic(asset.id),
      }));
      void client.invalidateQueries({ queryKey: ["assets", id] });
    },
  });
  const resetUploadMusic = uploadMusic.reset;
  const saveProjectProfile = useMutation({
    mutationFn: () =>
      api<Project>(`/projects/${id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: project!.name,
          description: project!.description,
          target_duration: project!.target_duration,
          aspect_ratio: project!.aspect_ratio,
          resolution_profile: projectProfile,
          fps: project!.fps,
          global_visual_style: project!.global_visual_style,
          global_negative_prompt: project!.global_negative_prompt,
        }),
      }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["project", id] }),
  });
  const saveStory = useMutation({
    mutationFn: () =>
      api(`/projects/${id}/story`, {
        method: "POST",
        body: JSON.stringify({ text: story }),
      }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["project", id] }),
  });
  const plan = useMutation({
    mutationFn: () =>
      api<Shot[]>(`/projects/${id}/plan/deterministic?auto_approve=true`, {
        method: "POST",
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["project", id] });
      void client.invalidateQueries({ queryKey: ["shots", id] });
    },
  });
  const addScene = useMutation({
    mutationFn: () =>
      api<Scene>(`/projects/${id}/scenes`, {
        method: "POST",
        body: JSON.stringify({
          number: Math.max(0, ...scenes.map((scene) => scene.number)) + 1,
          title: "New scene",
        }),
      }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["scenes", id] }),
  });
  const originalStory = project?.original_story;
  const storedProfile = project?.resolution_profile;
  useEffect(() => {
    if (originalStory !== undefined) setStory(originalStory);
  }, [originalStory]);
  useEffect(() => {
    setProjectProfile("");
    setRenderProfile("");
    setFinalization(createDefaultRenderFinalization());
    resetUploadMusic();
  }, [id, resetUploadMusic]);
  useEffect(() => {
    if (!storedProfile) return;
    setProjectProfile(storedProfile);
    setRenderProfile((current) => current || storedProfile);
  }, [id, storedProfile]);
  if (!project) return <p>Loading…</p>;
  const configuredProfiles = profileCatalog.data?.profiles ?? [];
  const projectProfileIsConfigured = configuredProfiles.some(
    (profile) => profile.name === projectProfile,
  );
  const renderProfileIsConfigured = configuredProfiles.some(
    (profile) => profile.name === renderProfile,
  );
  const audioAssets = assets.filter(
    (asset) =>
      asset.mime_type === "audio/wav" || asset.mime_type === "audio/mpeg",
  );
  return (
    <>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold">{project.name}</h1>
          <p className="text-slate-400">
            {project.aspect_ratio} · {project.resolution_profile} default
          </p>
        </div>
        <Link className="button" to={`/projects/${id}/characters`}>
          Characters & voices
        </Link>
      </div>
      {profileCatalog.isError && (
        <p className="card mb-6 text-red-300" role="alert">
          Render profiles could not be loaded. Existing project data remains
          available, but profile choices cannot be changed.
        </p>
      )}
      <div className="mb-6 grid gap-4 md:grid-cols-2">
        <section className="card">
          <h2 className="mb-3 text-xl font-bold">Project settings</h2>
          <RenderProfileSelect
            label="Default render profile"
            profiles={configuredProfiles}
            value={projectProfile}
            onChange={(profile) => {
              saveProjectProfile.reset();
              setProjectProfile(profile);
            }}
            disabled={
              profileCatalog.isPending ||
              profileCatalog.isError ||
              saveProjectProfile.isPending
            }
          />
          <button
            className="button mt-3"
            disabled={
              saveProjectProfile.isPending ||
              !projectProfile ||
              !projectProfileIsConfigured ||
              projectProfile === project.resolution_profile
            }
            onClick={() => saveProjectProfile.mutate()}
          >
            Save default profile
          </button>
          {saveProjectProfile.isSuccess && (
            <p className="mt-2 text-sm text-teal-200" aria-live="polite">
              Project default updated.
            </p>
          )}
          {saveProjectProfile.isError && (
            <p className="mt-2 text-sm text-red-300" role="alert">
              Profile update failed: {saveProjectProfile.error.message}
            </p>
          )}
        </section>
        <section className="card">
          <h2 className="mb-3 text-xl font-bold">Queue render</h2>
          <RenderProfileSelect
            label="Profile for this render"
            profiles={configuredProfiles}
            value={renderProfile}
            onChange={setRenderProfile}
            disabled={
              profileCatalog.isPending ||
              profileCatalog.isError ||
              render.isPending
            }
          />
          <p className="mt-2 text-xs text-slate-400">
            This choice applies to the queued render without changing the
            project default.
          </p>
          <RenderFinalizationControls
            value={finalization}
            audioAssets={audioAssets}
            onChange={(value) => {
              uploadMusic.reset();
              setFinalization(value);
            }}
            onUpload={(file) => uploadMusic.mutate(file)}
            disabled={render.isPending}
            uploadPending={uploadMusic.isPending}
            uploadError={uploadMusic.isError ? uploadMusic.error.message : ""}
            uploadSucceeded={uploadMusic.isSuccess}
          />
          {assetsQuery.isError && (
            <p className="mt-2 text-sm text-red-300" role="alert">
              Music assets could not be loaded: {assetsQuery.error.message}
            </p>
          )}
          <button
            className="button mt-3"
            disabled={
              shots.length === 0 ||
              render.isPending ||
              profileCatalog.isPending ||
              profileCatalog.isError ||
              !renderProfileIsConfigured
            }
            onClick={() =>
              render.mutate({
                render_profile: renderProfile,
                finalization,
              })
            }
            title={
              shots.length === 0
                ? "Add storyboard shots before rendering"
                : undefined
            }
          >
            Render with mocks
          </button>
          {render.isSuccess && (
            <p className="mt-2 text-sm text-teal-200" aria-live="polite">
              Render queued with the {render.variables.render_profile} profile,{" "}
              {render.variables.finalization.subtitle.mode} subtitles, and
              {render.variables.finalization.audio.normalize ? " " : " no "}
              loudness normalization.
            </p>
          )}
          {render.isError && (
            <p className="mt-2 text-sm text-red-300" role="alert">
              Render could not be queued: {render.error.message}
            </p>
          )}
        </section>
      </div>
      <section className="card mb-6">
        <h2 className="mb-3 text-xl font-bold">Story</h2>
        <textarea
          className="field min-h-36"
          value={story}
          onChange={(event) => setStory(event.target.value)}
          aria-label="Story text"
        />
        <div className="mt-3 flex items-center gap-3">
          <button
            className="button"
            disabled={saveStory.isPending || story === project.original_story}
            onClick={() => saveStory.mutate()}
          >
            Save story
          </button>
          {saveStory.isSuccess && (
            <span className="text-sm text-teal-200">Story saved</span>
          )}
          <button
            className="button"
            disabled={
              !story.trim() ||
              story !== project.original_story ||
              shots.length > 0 ||
              plan.isPending
            }
            onClick={() => plan.mutate()}
          >
            Plan mock storyboard
          </button>
        </div>
      </section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xl font-bold">Storyboard</h2>
        <button
          className="button"
          disabled={addScene.isPending}
          onClick={() => addScene.mutate()}
        >
          Add scene
        </button>
      </div>
      <div className="mb-5 grid gap-4 md:grid-cols-2">
        {scenes.map((scene, index) => (
          <SceneEditor
            key={scene.id}
            projectId={id!}
            scene={scene}
            shots={shots.filter((shot) => shot.scene_id === scene.id)}
            canMoveUp={index > 0}
            canMoveDown={index < scenes.length - 1}
          />
        ))}
      </div>
      {shots.length === 0 && (
        <p className="card text-slate-300">
          No shots yet. Add or plan shots before rendering.
        </p>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {shots.map((shot) => {
          const siblings = shots.filter(
            (item) => item.scene_id === shot.scene_id,
          );
          const index = siblings.findIndex((item) => item.id === shot.id);
          return (
            <ShotCard
              key={shot.id}
              projectId={id!}
              shot={shot}
              renderProfiles={configuredProfiles}
              defaultRenderProfile={project.resolution_profile}
              canMoveUp={index > 0}
              canMoveDown={index >= 0 && index < siblings.length - 1}
            />
          );
        })}
      </div>
    </>
  );
}

function ShotCard({
  projectId,
  shot,
  renderProfiles,
  defaultRenderProfile,
  canMoveUp,
  canMoveDown,
}: {
  projectId: string;
  shot: Shot;
  renderProfiles: RenderProfile[];
  defaultRenderProfile: string;
  canMoveUp: boolean;
  canMoveDown: boolean;
}) {
  const client = useQueryClient();
  const [prompt, setPrompt] = useState(shot.prompt);
  const [negativePrompt, setNegativePrompt] = useState(shot.negative_prompt);
  const [dialogue, setDialogue] = useState(shot.dialogue);
  const [narration, setNarration] = useState(shot.narration);
  const [speaker, setSpeaker] = useState(shot.speaker ?? "");
  const [shotType, setShotType] = useState(shot.shot_type);
  const [duration, setDuration] = useState(shot.duration);
  const [provider, setProvider] = useState(shot.provider);
  const [model, setModel] = useState(shot.model);
  const [seed, setSeed] = useState(shot.seed);
  const [transition, setTransition] = useState(shot.transition_type);
  const [overlap, setOverlap] = useState(shot.overlap_frame_count);
  const [framing, setFraming] = useState(shot.camera.framing ?? "medium");
  const [movement, setMovement] = useState(shot.camera.movement ?? "static");
  const initialCandidateCount =
    typeof shot.generation_settings.candidate_count === "number"
      ? shot.generation_settings.candidate_count
      : 1;
  const [candidateCount, setCandidateCount] = useState(initialCandidateCount);
  const save = useMutation({
    mutationFn: () =>
      api(`/shots/${shot.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          prompt,
          negative_prompt: negativePrompt,
          dialogue,
          narration,
          speaker: speaker || null,
          shot_type: shotType,
          duration,
          provider,
          model,
          seed,
          transition_type: transition,
          overlap_frame_count: overlap,
          camera: { ...shot.camera, framing, movement },
          generation_settings: {
            ...shot.generation_settings,
            candidate_count: candidateCount,
          },
        }),
      }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["shots", projectId] }),
  });
  const move = useMutation({
    mutationFn: (direction: "up" | "down") =>
      api(`/shots/${shot.id}/move`, {
        method: "POST",
        body: JSON.stringify({ direction }),
      }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["shots", projectId] }),
  });
  const remove = useMutation({
    mutationFn: () => api(`/shots/${shot.id}`, { method: "DELETE" }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["shots", projectId] }),
  });
  const approve = useMutation({
    mutationFn: () => api(`/shots/${shot.id}/approve`, { method: "POST" }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["shots", projectId] }),
  });
  const isDirty =
    prompt !== shot.prompt ||
    negativePrompt !== shot.negative_prompt ||
    dialogue !== shot.dialogue ||
    narration !== shot.narration ||
    speaker !== (shot.speaker ?? "") ||
    shotType !== shot.shot_type ||
    duration !== shot.duration ||
    provider !== shot.provider ||
    model !== shot.model ||
    seed !== shot.seed ||
    transition !== shot.transition_type ||
    overlap !== shot.overlap_frame_count ||
    framing !== (shot.camera.framing ?? "medium") ||
    movement !== (shot.camera.movement ?? "static") ||
    candidateCount !== initialCandidateCount;
  return (
    <article className="card">
      <div className="flex justify-between">
        <strong>
          Shot {shot.sequence_number} · {shot.shot_type}
        </strong>
        <span className="rounded bg-slate-700 px-2 py-1 text-xs">
          {shot.status}
        </span>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <label className="md:col-span-2">
          <span className="text-sm">Prompt</span>
          <textarea
            className="field"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            aria-label={`Shot ${shot.sequence_number} prompt`}
          />
        </label>
        <label className="md:col-span-2">
          <span className="text-sm">Negative prompt</span>
          <textarea
            className="field"
            value={negativePrompt}
            onChange={(event) => setNegativePrompt(event.target.value)}
          />
        </label>
        <label>
          <span className="text-sm">Shot type</span>
          <input
            className="field"
            value={shotType}
            onChange={(event) => setShotType(event.target.value)}
          />
        </label>
        <label>
          <span className="text-sm">Dialogue</span>
          <input
            className="field"
            value={dialogue}
            onChange={(e) => setDialogue(e.target.value)}
          />
        </label>
        <label className="md:col-span-2">
          <span className="text-sm">Narration</span>
          <input
            className="field"
            value={narration}
            onChange={(event) => setNarration(event.target.value)}
          />
        </label>
        <label>
          <span className="text-sm">Speaker</span>
          <input
            className="field"
            value={speaker}
            onChange={(e) => setSpeaker(e.target.value)}
          />
        </label>
        <NumberField
          label="Duration"
          value={duration}
          setValue={setDuration}
          min={0.1}
        />
        <NumberField label="Seed" value={seed} setValue={setSeed} />
        <label>
          <span className="text-sm">Provider</span>
          <input
            className="field"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          />
        </label>
        <label>
          <span className="text-sm">Model</span>
          <input
            className="field"
            value={model}
            onChange={(event) => setModel(event.target.value)}
          />
        </label>
        <NumberField
          label="Candidate count"
          value={candidateCount}
          setValue={setCandidateCount}
          min={1}
        />
        <label>
          <span className="text-sm">Transition</span>
          <select
            className="field"
            value={transition}
            onChange={(e) => setTransition(e.target.value)}
          >
            <option value="hard_cut">Hard cut</option>
            <option value="shared_frame">Shared frame</option>
            <option value="crossfade">Crossfade</option>
            <option value="match_cut">Match cut</option>
            <option value="interpolated_bridge">Interpolated bridge</option>
          </select>
        </label>
        <NumberField
          label="Overlap frames"
          value={overlap}
          setValue={setOverlap}
          min={0}
        />
        <label>
          <span className="text-sm">Camera framing</span>
          <input
            className="field"
            value={framing}
            onChange={(e) => setFraming(e.target.value)}
          />
        </label>
        <label>
          <span className="text-sm">Camera movement</span>
          <input
            className="field"
            value={movement}
            onChange={(e) => setMovement(e.target.value)}
          />
        </label>
      </div>
      <div className="mb-3 flex flex-wrap gap-2">
        <button
          className="button"
          disabled={save.isPending || !isDirty}
          onClick={() => save.mutate()}
        >
          Save shot
        </button>
        {shot.approval_state !== "approved" && (
          <button
            className="button"
            disabled={approve.isPending}
            onClick={() => approve.mutate()}
          >
            Approve shot
          </button>
        )}
        <button
          className="button"
          aria-label={`Move shot ${shot.sequence_number} up`}
          disabled={!canMoveUp || move.isPending}
          onClick={() => move.mutate("up")}
        >
          Move up
        </button>
        <button
          className="button"
          aria-label={`Move shot ${shot.sequence_number} down`}
          disabled={!canMoveDown || move.isPending}
          onClick={() => move.mutate("down")}
        >
          Move down
        </button>
        <button
          className="button"
          aria-label={`Delete shot ${shot.sequence_number}`}
          disabled={remove.isPending}
          onClick={() => {
            if (window.confirm(`Delete shot ${shot.sequence_number}?`)) {
              remove.mutate();
            }
          }}
        >
          Delete shot
        </button>
      </div>
      <p>
        {shot.speaker && <b>{shot.speaker}: </b>}
        {shot.dialogue || "No dialogue"}
      </p>
      <dl className="mt-3 grid grid-cols-2 text-sm text-slate-400">
        <dt>Duration</dt>
        <dd>{shot.duration}s</dd>
        <dt>Provider</dt>
        <dd>{shot.provider}</dd>
        <dt>Transition</dt>
        <dd>{shot.transition_type}</dd>
        <dt>Continuity</dt>
        <dd>{shot.continuity_source_frame_id ? "chained" : "independent"}</dd>
      </dl>
      <details className="mt-3">
        <summary>Continuity packet</summary>
        <pre className="mt-2 overflow-auto text-xs">
          {JSON.stringify(shot.continuity_packet, null, 2)}
        </pre>
      </details>
      <CandidatePanel
        shot={shot}
        renderProfiles={renderProfiles}
        defaultRenderProfile={defaultRenderProfile}
      />
    </article>
  );
}

function NumberField({
  label,
  value,
  setValue,
  min,
}: {
  label: string;
  value: number;
  setValue: (value: number) => void;
  min?: number;
}) {
  return (
    <label>
      <span className="text-sm">{label}</span>
      <input
        className="field"
        type="number"
        min={min}
        value={value}
        onChange={(event) => setValue(Number(event.target.value))}
      />
    </label>
  );
}

function SceneEditor({
  projectId,
  scene,
  shots,
  canMoveUp,
  canMoveDown,
}: {
  projectId: string;
  scene: Scene;
  shots: Shot[];
  canMoveUp: boolean;
  canMoveDown: boolean;
}) {
  const client = useQueryClient();
  const [title, setTitle] = useState(scene.title);
  const [location, setLocation] = useState(scene.location);
  const [lighting, setLighting] = useState(scene.lighting);
  const save = useMutation({
    mutationFn: () =>
      api(`/scenes/${scene.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title, location, lighting }),
      }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["scenes", projectId] }),
  });
  const addShot = useMutation({
    mutationFn: () =>
      api<Shot>(`/scenes/${scene.id}/shots`, {
        method: "POST",
        body: JSON.stringify({
          sequence_number:
            Math.max(0, ...shots.map((shot) => shot.sequence_number)) + 1,
          duration: 3,
          prompt: "New storyboard shot",
        }),
      }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["shots", projectId] }),
  });
  const move = useMutation({
    mutationFn: (direction: "up" | "down") =>
      api(`/scenes/${scene.id}/move`, {
        method: "POST",
        body: JSON.stringify({ direction }),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["scenes", projectId] });
      void client.invalidateQueries({ queryKey: ["shots", projectId] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/scenes/${scene.id}`, { method: "DELETE" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["scenes", projectId] });
      void client.invalidateQueries({ queryKey: ["shots", projectId] });
    },
  });
  return (
    <section className="card">
      <h3 className="font-bold">Scene {scene.number}</h3>
      <input
        className="field mt-2"
        aria-label={`Scene ${scene.number} title`}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <input
        className="field mt-2"
        aria-label={`Scene ${scene.number} location`}
        value={location}
        onChange={(e) => setLocation(e.target.value)}
      />
      <input
        className="field mt-2"
        aria-label={`Scene ${scene.number} lighting`}
        value={lighting}
        onChange={(e) => setLighting(e.target.value)}
      />
      <div className="mt-2 flex flex-wrap gap-2">
        <button className="button" onClick={() => save.mutate()}>
          Save scene
        </button>
        <button
          className="button"
          aria-label={`Add shot to scene ${scene.number}`}
          disabled={addShot.isPending}
          onClick={() => addShot.mutate()}
        >
          Add shot
        </button>
        <button
          className="button"
          aria-label={`Move scene ${scene.number} up`}
          disabled={!canMoveUp || move.isPending}
          onClick={() => move.mutate("up")}
        >
          Move up
        </button>
        <button
          className="button"
          aria-label={`Move scene ${scene.number} down`}
          disabled={!canMoveDown || move.isPending}
          onClick={() => move.mutate("down")}
        >
          Move down
        </button>
        <button
          className="button"
          aria-label={`Delete scene ${scene.number}`}
          disabled={remove.isPending}
          onClick={() => {
            if (
              window.confirm(
                `Delete scene ${scene.number} and its ${shots.length} shots?`,
              )
            ) {
              remove.mutate();
            }
          }}
        >
          Delete scene
        </button>
      </div>
    </section>
  );
}

function CandidatePanel({
  shot,
  renderProfiles,
  defaultRenderProfile,
}: {
  shot: Shot;
  renderProfiles: RenderProfile[];
  defaultRenderProfile: string;
}) {
  const client = useQueryClient();
  const [regenerationProfile, setRegenerationProfile] =
    useState(defaultRenderProfile);
  const { data: candidates = [] } = useQuery({
    queryKey: ["candidates", shot.id],
    queryFn: () => api<Candidate[]>(`/shots/${shot.id}/candidates`),
  });
  const regenerate = useMutation({
    mutationFn: (sameSeed: boolean) =>
      enqueueShotRegeneration(shot.id, sameSeed, regenerationProfile),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["jobs"] }),
  });
  useEffect(() => {
    setRegenerationProfile(defaultRenderProfile);
  }, [defaultRenderProfile]);
  const regenerationProfileIsConfigured = renderProfiles.some(
    (profile) => profile.name === regenerationProfile,
  );
  async function action(candidateId: string, operation: "select" | "reject") {
    const path =
      operation === "select"
        ? `/shots/${shot.id}/candidates/${candidateId}/select`
        : `/candidates/${candidateId}/reject`;
    await api(path, { method: "POST" });
    await client.invalidateQueries({ queryKey: ["candidates", shot.id] });
  }
  async function rate(candidateId: string, rating: number) {
    await api(`/candidates/${candidateId}/rating`, {
      method: "POST",
      body: JSON.stringify({ rating }),
    });
    await client.invalidateQueries({ queryKey: ["candidates", shot.id] });
  }
  return (
    <section className="mt-4 border-t border-slate-700 pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-bold">Candidates ({candidates.length})</h3>
      </div>
      <div className="mt-3 grid items-end gap-2 md:grid-cols-[1fr_auto_auto]">
        <RenderProfileSelect
          label={`Shot ${shot.sequence_number} regeneration profile`}
          profiles={renderProfiles}
          value={regenerationProfile}
          onChange={setRegenerationProfile}
          disabled={renderProfiles.length === 0 || regenerate.isPending}
        />
        <button
          className="button"
          disabled={regenerate.isPending || !regenerationProfileIsConfigured}
          onClick={() => regenerate.mutate(true)}
        >
          Regenerate same seed
        </button>
        <button
          className="button"
          disabled={regenerate.isPending || !regenerationProfileIsConfigured}
          onClick={() => regenerate.mutate(false)}
        >
          Regenerate new seed
        </button>
      </div>
      {regenerate.isError && (
        <p className="mt-2 text-sm text-red-300" role="alert">
          Regeneration could not be queued: {regenerate.error.message}
        </p>
      )}
      {candidates.map((candidate) => (
        <article
          className="mt-3 rounded border border-slate-600 p-3"
          key={candidate.id}
        >
          <div className="flex flex-wrap justify-between gap-2">
            <span>
              {candidate.provider} · seed {candidate.seed} ·{" "}
              {candidate.disposition}
            </span>
            {candidate.output_asset_id && (
              <a
                className="text-teal-300 underline"
                href={`/api/v1/assets/${candidate.output_asset_id}/file`}
              >
                Preview video
              </a>
            )}
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              className="button"
              onClick={() => void action(candidate.id, "select")}
            >
              Select
            </button>
            <button
              className="button"
              onClick={() => void action(candidate.id, "reject")}
            >
              Reject
            </button>
            <label>
              <span className="sr-only">Candidate rating</span>
              <select
                className="field"
                value={candidate.user_rating ?? ""}
                onChange={(e) =>
                  void rate(candidate.id, Number(e.target.value))
                }
              >
                <option value="">Rate</option>
                {[1, 2, 3, 4, 5].map((value) => (
                  <option value={value} key={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <details className="mt-2">
            <summary>QA results</summary>
            <pre className="overflow-auto text-xs">
              {JSON.stringify(candidate.qa_results, null, 2)}
            </pre>
          </details>
        </article>
      ))}
    </section>
  );
}

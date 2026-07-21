# ADR 0021: Require exact-duration input and verified runtime evidence for local lip sync

**Status:** Accepted
**Date:** 2026-07-21

## Context

LatentSync 1.5 was implemented behind a local subprocess adapter but had not run against real
weights. Its upstream preprocessing and evaluator assume 25-fps media and repository-relative
checkpoint paths. A production exercise exposed two defects hidden by protocol fixtures: the raw
RIFE 25-fps result contained 321 frames over 12.84 seconds for an 81-frame source, and the evaluator
could not locate `checkpoints/` from its collision-safe isolated working directory.

Path existence also did not prove that a configured multi-gigabyte runtime was authentic, complete,
or CUDA-capable. Finally, a returned video alone could not establish useful A/V synchronization.

## Decision

Persist the raw pre-lip-sync interpolation as an immutable Asset, then create and persist a separate
exact 250-frame, CFR-25, 10.000-second input before calling LatentSync. Keep evaluator workspaces
unique, but expose the administrator-controlled pinned checkpoint directory through a workspace
symlink. Health requires the pinned official repository revision, hashes of all six required model
files, and a real CUDA/dependency import probe; hashing runs outside the API event loop.

Exercise the post-stage on an already accepted real Wan chain clip. Acceptance requires official
SyncNet confidence at least 3, offset within one frame, audio, exact 600-frame CFR-60 delivery,
ordinary start/end/no-snap QA, checksum-bound visual review, immutable Asset lineage, and physical-GPU
telemetry. Retain an unsuitable audio attempt as negative evidence. Keep all media, audio, databases,
weights, and model caches outside Git.

## Alternatives

- Passing raw RIFE output directly was rejected because requested rate did not imply exact duration
  or frame count.
- Running evaluation in the repository root was rejected because fixed upstream intermediate names
  can collide across workers.
- Treating file presence or a successful adapter return as health/quality was rejected because it
  misses corrupt weights, missing dependencies, unavailable CUDA, and poor synchronization.
- LatentSync 1.6 was rejected for this workstation because its documented minimum exceeds one 12 GB
  GPU. No pooled 24 GB assumption is made.

## Consequences

LatentSync 1.5 is **Exercised** on GPU 1 for one eligible single-visible-speaker sample: confidence
6.81, offset 0, exact 600-frame audio delivery, passing endpoints, and passing visual review. Peak
observed allocation was 7,629 MiB. One mismatched source/audio attempt failed at confidence 0.16 and
offset 3, demonstrating an honest rejection path. The code is Apache-2.0; official weights are
OpenRAIL++, and operators must review those restrictions plus source-audio rights. Evidence does not
generalize to arbitrary speakers, languages, occlusion, or multiple-face selection.

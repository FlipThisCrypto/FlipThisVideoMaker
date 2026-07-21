import asyncio
import base64
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from flipthis_video_maker.contracts.video_generation import (
    GenerationCategory,
    LipSyncMode,
    utc_now,
)
from flipthis_video_maker.media.ffmpeg import probe
from flipthis_video_maker.providers.base.errors import (
    ProviderExecutionError,
    ProviderFailureKind,
)
from flipthis_video_maker.providers.base.models import Capability, ProviderInfo
from flipthis_video_maker.providers.base.video_generation import (
    ProviderRunOutput,
    ResolvedFirstLastFrameRequest,
)

ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]
_CAMERA_MOTIONS = {
    "dolly_in",
    "dolly_out",
    "dolly_left",
    "dolly_right",
    "jib_up",
    "jib_down",
    "static",
    "focus_shift",
}


class LtxVideoProvider:
    """LTX-2.3 async API adapter for documented first/last-frame generation."""

    def __init__(
        self,
        provider_id: str,
        *,
        api_key: str | None,
        endpoint: str = "https://api.ltx.video",
        model: str = "ltx-2-3-pro",
        timeout_seconds: float = 900,
        poll_interval_seconds: float = 5,
        max_download_mb: int = 2048,
        max_inline_image_mb: int = 7,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.api_key = api_key.strip() if api_key else None
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.max_download_bytes = max_download_mb * 1024 * 1024
        self.max_inline_image_bytes = max_inline_image_mb * 1024 * 1024
        self.transport = transport

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id=self.provider_id,
            name=f"LTX-2.3 {'Pro' if self.model.endswith('pro') else 'Fast'} first/last video",
            model_identity=self.model,
            capabilities={
                Capability.VIDEO_GENERATION,
                Capability.FIRST_LAST_FRAME_GENERATIVE_VIDEO,
                Capability.AUDIO_GENERATION,
            },
            available=self.api_key is not None,
            supported_inputs={"image/png", "image/jpeg", "prompt", "camera_motion"},
            max_duration_seconds=10,
            max_width=3840,
            max_height=2160,
            native_frame_rates={24, 48},
            supported_durations_seconds={6, 8, 10},
            generation_category=GenerationCategory.FIRST_LAST_FRAME_GENERATIVE_VIDEO,
            cancellation_supported=False,
            progress_supported=False,
            notes=(
                "True LTX-2.3 first/last-frame async generation. The adapter disables "
                "provider-generated audio so requested dialogue remains a separate Asset. "
                "The documented V2 API exposes no cancellation endpoint."
            ),
        )

    async def health(self) -> dict[str, object]:
        if self.api_key is None:
            return {"ok": False, "status": "missing_credentials"}
        try:
            async with self._client() as client:
                response = await client.get(
                    "/v2/image-to-video/00000000-0000-0000-0000-000000000000"
                )
        except httpx.HTTPError as error:
            return {"ok": False, "status": "unreachable", "error_type": type(error).__name__}
        if response.status_code in {200, 404}:
            return {"ok": True, "status": "authenticated", "api_version": "v2"}
        return {
            "ok": False,
            "status": "authentication_failed"
            if response.status_code in {401, 403}
            else "unexpected_response",
            "http_status": response.status_code,
        }

    async def generate(
        self,
        request: ResolvedFirstLastFrameRequest,
        *,
        cancel_requested: CancelCheck | None = None,
        progress: ProgressCallback | None = None,
    ) -> ProviderRunOutput:
        self._validate_request(request)
        if self.api_key is None:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="generate_video",
                failure_kind=ProviderFailureKind.AUTHENTICATION,
                backend_code="missing_credentials",
            )
        self._raise_if_cancelled(cancel_requested)
        submitted_at = utc_now()
        started = time.monotonic()
        provider_job_id: str | None = None
        try:
            async with self._client() as client:
                response = await client.post("/v2/image-to-video", json=self._payload(request))
                self._raise_for_http(response, operation="submit")
                body = self._response_json(response, operation="submit")
                provider_job_id = self._required_string(body, "id", operation="submit")
                if progress:
                    progress(0.1, "provider_pending")
                terminal = await self._poll(
                    client,
                    provider_job_id,
                    cancel_requested=cancel_requested,
                    progress=progress,
                )
                provider_seconds = time.monotonic() - started
                output_url = self._video_output_url(terminal, provider_job_id)
                if progress:
                    progress(0.8, "downloading_native_video")
                download_started = time.monotonic()
                await self._download(
                    output_url,
                    request.output_path,
                    cancel_requested=cancel_requested,
                    provider_job_id=provider_job_id,
                )
                download_seconds = time.monotonic() - download_started
        except ProviderExecutionError:
            raise
        except httpx.TimeoutException as error:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="generate_video",
                failure_kind=ProviderFailureKind.TIMEOUT,
                retryable=True,
                backend_code="client_timeout",
                provider_job_id=provider_job_id,
            ) from error
        except httpx.HTTPError as error:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="generate_video",
                retryable=True,
                backend_code=type(error).__name__,
                provider_job_id=provider_job_id,
            ) from error
        self._validate_output(request.output_path, provider_job_id)
        settings = request.snapshot.provider_settings.get(self.provider_id, {})
        return ProviderRunOutput(
            output_path=request.output_path,
            provider_job_id=provider_job_id,
            actual_model=self.model,
            api_version="v2",
            submitted_at=submitted_at,
            completed_at=utc_now(),
            provider_seconds=provider_seconds,
            download_seconds=download_seconds,
            actual_settings={
                "duration": int(request.snapshot.duration_seconds),
                "fps": request.snapshot.native_requested_fps,
                "resolution": f"{request.snapshot.width}x{request.snapshot.height}",
                "generate_audio": False,
                **settings,
            },
            warnings=(
                "Provider does not document server-side cancellation",
                "Terminal status does not echo model/settings; recorded from submitted request",
            ),
        )

    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        return httpx.AsyncClient(
            base_url=self.endpoint,
            headers=headers,
            timeout=httpx.Timeout(30, read=60),
            transport=self.transport,
            follow_redirects=False,
        )

    def _validate_request(self, request: ResolvedFirstLastFrameRequest) -> None:
        snapshot = request.snapshot
        if request.output_path.exists():
            raise ValueError("LTX output destination already exists")
        if snapshot.provider_id != self.provider_id or snapshot.provider_model != self.model:
            raise ValueError("Request provider identity does not match the LTX adapter")
        if snapshot.duration_seconds not in {6, 8, 10}:
            raise ValueError("LTX-2.3 supports the requested profile only at 6, 8, or 10 seconds")
        if snapshot.native_requested_fps not in {24, 48}:
            raise ValueError("LTX-2.3 native FPS must be 24 or 48")
        if (snapshot.width, snapshot.height) not in {
            (1920, 1080),
            (1080, 1920),
            (2560, 1440),
            (1440, 2560),
            (3840, 2160),
            (2160, 3840),
        }:
            raise ValueError("Unsupported LTX-2.3 resolution")
        if snapshot.negative_prompt:
            raise ValueError("LTX image-to-video does not document negative_prompt")
        if snapshot.seed is not None or snapshot.motion_strength is not None:
            raise ValueError("LTX image-to-video does not document seed or motion strength")
        if snapshot.identity_reference_asset_ids:
            raise ValueError("LTX FLF generation does not accept identity reference inputs")
        if (
            snapshot.audio_reference_asset_id is not None
            and snapshot.lip_sync_mode is not LipSyncMode.LATENTSYNC
        ):
            raise ValueError("LTX FLF generation does not accept audio conditioning")
        settings = snapshot.provider_settings.get(self.provider_id, {})
        if set(settings) - {"camera_motion"}:
            raise ValueError("Unsupported LTX provider-specific setting")
        camera_motion = settings.get("camera_motion")
        if camera_motion is not None and camera_motion not in _CAMERA_MOTIONS:
            raise ValueError("Unsupported LTX camera_motion")
        for path, mime_type in (
            (request.start_frame_path, request.start_frame_mime_type),
            (request.end_frame_path, request.end_frame_mime_type),
        ):
            if mime_type not in {"image/png", "image/jpeg"} or not path.is_file():
                raise ValueError("LTX keyframes must resolve to PNG or JPEG Assets")
            if len(self._data_uri(path, mime_type).encode("ascii")) > self.max_inline_image_bytes:
                raise ValueError("LTX keyframe exceeds the configured encoded Data URI limit")

    def _payload(self, request: ResolvedFirstLastFrameRequest) -> dict[str, object]:
        snapshot = request.snapshot
        payload: dict[str, object] = {
            "image_uri": self._data_uri(
                request.start_frame_path,
                request.start_frame_mime_type,
            ),
            "last_frame_uri": self._data_uri(
                request.end_frame_path,
                request.end_frame_mime_type,
            ),
            "prompt": self._combined_prompt(snapshot.prompt, snapshot.camera_direction),
            "model": self.model,
            "duration": int(snapshot.duration_seconds),
            "fps": snapshot.native_requested_fps,
            "resolution": f"{snapshot.width}x{snapshot.height}",
            "generate_audio": False,
        }
        payload.update(snapshot.provider_settings.get(self.provider_id, {}))
        return payload

    @staticmethod
    def _combined_prompt(prompt: str, camera_direction: str) -> str:
        return prompt if camera_direction == "natural" else f"{prompt}\nCamera: {camera_direction}"

    @staticmethod
    def _data_uri(path: Path, mime_type: str) -> str:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{data}"

    async def _poll(
        self,
        client: httpx.AsyncClient,
        provider_job_id: str,
        *,
        cancel_requested: CancelCheck | None,
        progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            self._raise_if_cancelled(cancel_requested, provider_job_id)
            if time.monotonic() >= deadline:
                raise ProviderExecutionError(
                    provider_id=self.provider_id,
                    operation="poll",
                    failure_kind=ProviderFailureKind.TIMEOUT,
                    retryable=True,
                    backend_code="poll_deadline",
                    provider_job_id=provider_job_id,
                )
            response = await client.get(f"/v2/image-to-video/{provider_job_id}")
            self._raise_for_http(response, operation="poll", provider_job_id=provider_job_id)
            body = self._response_json(response, operation="poll")
            status = self._required_string(body, "status", operation="poll")
            if status == "completed":
                return body
            if status == "failed":
                self._raise_provider_failure(body, provider_job_id)
            if status not in {"pending", "processing"}:
                raise ProviderExecutionError(
                    provider_id=self.provider_id,
                    operation="poll",
                    failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                    backend_code="unknown_status",
                    provider_job_id=provider_job_id,
                )
            if progress:
                progress(0.2 if status == "pending" else 0.5, f"provider_{status}")
            await asyncio.sleep(self.poll_interval_seconds)

    async def _download(
        self,
        url: str,
        destination: Path,
        *,
        cancel_requested: CancelCheck | None,
        provider_job_id: str,
    ) -> None:
        parsed = httpx.URL(url)
        if parsed.scheme != "https" or not parsed.host:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="download",
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="unsafe_output_url",
                provider_job_id=provider_job_id,
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f".{destination.name}.{provider_job_id}.partial")
        size = 0
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30, read=300),
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                async with client.stream("GET", url) as response:
                    self._raise_for_http(
                        response,
                        operation="download",
                        provider_job_id=provider_job_id,
                    )
                    content_type = response.headers.get("content-type", "").split(";")[0]
                    if content_type and content_type not in {
                        "video/mp4",
                        "application/octet-stream",
                    }:
                        raise ProviderExecutionError(
                            provider_id=self.provider_id,
                            operation="download",
                            failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                            backend_code="unexpected_content_type",
                            provider_job_id=provider_job_id,
                        )
                    with partial.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            self._raise_if_cancelled(cancel_requested, provider_job_id)
                            size += len(chunk)
                            if size > self.max_download_bytes:
                                raise ProviderExecutionError(
                                    provider_id=self.provider_id,
                                    operation="download",
                                    failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                                    backend_code="output_too_large",
                                    provider_job_id=provider_job_id,
                                )
                            handle.write(chunk)
            if size == 0:
                raise ProviderExecutionError(
                    provider_id=self.provider_id,
                    operation="download",
                    failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                    backend_code="empty_output",
                    provider_job_id=provider_job_id,
                )
            partial.replace(destination)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def _raise_for_http(
        self,
        response: httpx.Response,
        *,
        operation: str,
        provider_job_id: str | None = None,
    ) -> None:
        if response.status_code < 400:
            return
        retry_after: float | None = None
        try:
            retry_after = float(response.headers["Retry-After"])
        except (KeyError, ValueError):
            pass
        error_type = self._http_error_type(response)
        mapping = {
            "authentication_error": ProviderFailureKind.AUTHENTICATION,
            "insufficient_funds_error": ProviderFailureKind.BUDGET_EXHAUSTED,
            "content_filtered_error": ProviderFailureKind.CONTENT_MODERATED,
            "invalid_request_error": ProviderFailureKind.INVALID_INPUT,
            "rate_limit_error": ProviderFailureKind.RATE_LIMITED,
            "concurrency_limit_error": ProviderFailureKind.RATE_LIMITED,
            "not_found_error": ProviderFailureKind.INVALID_INPUT,
        }
        kind = mapping.get(error_type or "", ProviderFailureKind.EXECUTION_FAILED)
        retryable = error_type in {
            "rate_limit_error",
            "concurrency_limit_error",
            "api_error",
            "service_unavailable",
            "overloaded_error",
        }
        raise ProviderExecutionError(
            provider_id=self.provider_id,
            operation=operation,
            failure_kind=kind,
            retryable=retryable,
            backend_code=error_type or f"http_{response.status_code}",
            provider_job_id=provider_job_id,
            retry_after_seconds=retry_after,
        )

    @staticmethod
    def _http_error_type(response: httpx.Response) -> str | None:
        try:
            body = response.json()
        except ValueError:
            return None
        error = body.get("error") if isinstance(body, dict) else None
        value = error.get("type") if isinstance(error, dict) else None
        return value if isinstance(value, str) and value else None

    def _raise_provider_failure(self, body: dict[str, Any], provider_job_id: str) -> None:
        error = body.get("error")
        error_type = error.get("type") if isinstance(error, dict) else None
        response = httpx.Response(
            500,
            json={"type": "error", "error": {"type": error_type or "api_error", "message": ""}},
        )
        self._raise_for_http(response, operation="generate_video", provider_job_id=provider_job_id)

    def _validate_output(self, path: Path, provider_job_id: str) -> None:
        try:
            media = probe(path)
        except Exception as error:
            path.unlink(missing_ok=True)
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="validate_output",
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="ffprobe_failed",
                provider_job_id=provider_job_id,
            ) from error
        streams = media.get("streams", [])
        if not any(
            isinstance(stream, dict) and stream.get("codec_type") == "video" for stream in streams
        ):
            path.unlink(missing_ok=True)
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="validate_output",
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="missing_video_stream",
                provider_job_id=provider_job_id,
            )

    def _video_output_url(self, body: dict[str, Any], provider_job_id: str) -> str:
        result = body.get("result")
        value = result.get("video_url") if isinstance(result, dict) else None
        if isinstance(value, str) and value:
            return value
        raise ProviderExecutionError(
            provider_id=self.provider_id,
            operation="collect_output",
            failure_kind=ProviderFailureKind.OUTPUT_INVALID,
            backend_code="missing_video_url",
            provider_job_id=provider_job_id,
        )

    def _response_json(self, response: httpx.Response, *, operation: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as error:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation=operation,
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="invalid_json",
            ) from error
        if not isinstance(body, dict):
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation=operation,
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="invalid_response_shape",
            )
        return body

    def _required_string(self, body: dict[str, Any], key: str, *, operation: str) -> str:
        value = body.get(key)
        if not isinstance(value, str) or not value:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation=operation,
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code=f"missing_{key}",
            )
        return value

    def _raise_if_cancelled(
        self,
        cancel_requested: CancelCheck | None,
        provider_job_id: str | None = None,
    ) -> None:
        if cancel_requested is not None and cancel_requested():
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="generate_video",
                failure_kind=ProviderFailureKind.CANCELLED,
                backend_code="local_polling_cancelled",
                provider_job_id=provider_job_id,
            )


__all__ = ["LtxVideoProvider"]

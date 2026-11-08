"""The render features this worker version can do (LIVESTACK-50).

Declared at enrollment (WorkerCapabilities.render_features) and in every
heartbeat, so the orchestrator offers this worker only renders it can
make (docs/api.md §5 "Render features"). A worker version that learns
something new adds it here; the orchestrator's side is
orchestrator/domain/render_features.py.
"""

from shared.models import RenderFeature

DECLARED_RENDER_FEATURES = [
    # Installer 0.4.0, LIVESTACK-50: "Girar" and fitting any picture into
    # 1920×1080 (shared.framing), and "Sem áudio".
    RenderFeature.FRAMING.value,
    RenderFeature.NO_AUDIO.value,
    # Installer 0.5.0, LIVESTACK-47: the passagem's official result as a
    # third pill under the lower third (worker.overlay_result).
    RenderFeature.PASS_RESULT.value,
    # Installer 0.6.0, LIVESTACK-48: the running clock, from the largada to
    # the official time (worker.overlay_clock, shared.ffmpeg_clock).
    RenderFeature.CLOCK.value,
    # Installer 0.7.0, LIVESTACK-53: the clock top right, bigger, with its
    # unit, and the result pill from the clock's freeze on.
    RenderFeature.CLOCK_TOP_RIGHT.value,
]

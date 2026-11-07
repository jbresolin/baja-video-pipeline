"""The render features this worker version can do (LIVESTACK-50).

Declared at enrollment (WorkerCapabilities.render_features) and in every
heartbeat, so the orchestrator offers this worker only renders it can
make (docs/api.md §5 "Render features"). A worker version that learns
something new adds it here; the orchestrator's side is
orchestrator/domain/render_features.py.
"""

from shared.models import RenderFeature

# Installer 0.4.0: "Girar" and fitting any picture into 1920×1080
# (shared.framing), and "Sem áudio".
DECLARED_RENDER_FEATURES = [RenderFeature.FRAMING.value, RenderFeature.NO_AUDIO.value]

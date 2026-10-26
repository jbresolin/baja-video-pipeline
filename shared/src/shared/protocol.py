"""Constants that the orchestrator, workers and the studio must agree on.

Keeping them in one place means a change here changes every side at once.
"""

# --- Timing (design §5: 15 s heartbeats; a lease dies after missed beats) ---

HEARTBEAT_INTERVAL_SECONDS = 15
# Three missed heartbeats and the lease is gone; the job goes back to the queue.
LEASE_TTL_SECONDS = 45
# How long a worker should wait before asking /next again when it got nothing.
EMPTY_QUEUE_RETRY_SECONDS = 5

# --- Chunked upload protocol (see docs/api.md, "Chunked uploads") ---

# Byte offset, inside the whole file, where this request's body starts.
UPLOAD_OFFSET_HEADER = "Upload-Offset"
# Total size of the whole file in bytes (the same on every chunk).
UPLOAD_LENGTH_HEADER = "Upload-Length"
# SHA-256 (lowercase hex) of the whole file; the receiver checks it at the end.
UPLOAD_SHA256_HEADER = "Upload-SHA256"
# Identifies the lease under which a worker uploads a proxy, thumbnail or clip.
LEASE_ID_HEADER = "Lease-Id"
# Where the studio should store a pushed clip, relative to the box root.
CLIP_RELATIVE_PATH_HEADER = "Clip-Relative-Path"
# Shared secret the Evolution webhook must send so random posts are rejected.
WEBHOOK_SECRET_HEADER = "X-Webhook-Secret"

DEFAULT_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024

# --- Ports (design §8, "Ports (contract)") ---

# Caddy's tailnet-side listener (plain HTTP on the VPS Tailscale IP): the
# worker/studio API plus the /inbox/* and /outbox/* files.
CADDY_TAILNET_PORT = 8080
# Worker-hosted file server: peers fetch held raws from GET /raw/{job_id}.
DEFAULT_WORKER_FILE_SERVER_PORT = 8710
# Studio-hosted receiver: workers push finished clips to POST /receive/{job_id}.
DEFAULT_STUDIO_RECEIVER_PORT = 8720

# --- Rate limit on the one public API route (design §15) ---

# POST /enroll: at most this many requests per source IP per window.
ENROLL_RATE_LIMIT_REQUESTS = 10
ENROLL_RATE_LIMIT_WINDOW_SECONDS = 600

# --- Holders (design §4) ---

# Every raw must be held by this many workers before the VPS deletes its copy.
REQUIRED_HOLDER_COUNT = 2

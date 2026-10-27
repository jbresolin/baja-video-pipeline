"""The orchestrator: intake, jobs DB, scheduling, review API and delivery (design §2).

Layout:
    config.py       BVP_* settings (fail fast when one is missing)
    app.py          build_context() + create_app(); __main__.py runs uvicorn
    context.py      everything a handler needs, in one object
    db/             SQLite connection, WAL, numbered SQL migrations
    repositories/   plain SQL, one module per table group
    domain/         the rules: intake, enrollment, scheduling, leases, review, delivery
    clients/        Evolution (bot replies), Tailscale (auth keys), Baja app (teams)
    api/            FastAPI routes, auth dependencies, error shape
    background/     lease expiry, inbox cleanup, proxy eviction, outbox cleanup
    storage.py      files under BVP_DATA_DIR and the URLs pointing at them
"""

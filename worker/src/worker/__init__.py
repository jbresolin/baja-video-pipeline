"""Worker app (roles: master, worker, studio). Run it with `python -m worker`.

Modules, roughly in the order work flows through them:
    __main__        entry point: setup page, enrollment, then the daemon
    config          worker.json
    enrollment      first-run POST /enroll, machine capabilities
    setup_page      the localhost setup/status page
    daemon          builds and starts/stops every piece below
    heartbeat       15 s heartbeat: leases, held raws, free space
    dispatcher      /next + /claim, hands tasks to the stages
    stages          one thread per task kind; turns outcomes into reports
    tasks/          download_raw, make_proxy, process (+ clip_delivery), deliver
    storage         working folder layout and the storage cap
    media_tools     runs ffprobe/ffmpeg, stoppable mid-render
    overlay         the overlay PNG (Pillow)
    servers         peer file server (:8710) and the uvicorn thread runner
    studio/         clip folder + latest.m3u, receiver (:8720), delivered ACKs
    backfill        master: copy raws missed while offline from peers
    leases, cancellable_transport, api_client, logging_setup, status: plumbing
"""

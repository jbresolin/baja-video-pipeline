"""Code shared by the orchestrator, the worker and the studio receiver.

Modules:
    models         pydantic models for jobs, workers and every API body
    state_machine  which job state changes are allowed
    hashing        streaming SHA-256 of files
    http_transfer  resumable downloads and chunked uploads (client side)
    upload_receiver  the server side of the chunked upload protocol
    ffmpeg         ffmpeg command builders and encoder detection
    layout         clip folder layout on the studio box and latest.m3u
    protocol       constants every side must agree on (headers, timings)
"""

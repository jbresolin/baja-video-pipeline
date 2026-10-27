"""Review UI service (design §6 and §16).

A small web app that reviewers open in a browser at /review. It keeps no
data of its own: every page is built from the orchestrator's HTTP API,
called with the reviewer's own credentials (the browser's Authorization
header, which Caddy has already checked).
"""

__version__ = "0.1.0"

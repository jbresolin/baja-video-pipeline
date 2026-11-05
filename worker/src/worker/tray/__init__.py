"""The Windows notification-area icon, BajaWorkerTray.exe (LIVESTACK-38 item 9).

    state     status JSON + service state → colour, tooltip, menu, toasts.
              No GUI imports, fully unit-tested on Linux.
    service   asking Windows about / starting / stopping the BajaWorker
              service (`sc.exe`), with the output parsing kept testable.
    app       the thin pystray layer: draws the icon, polls, runs the menu.

The tray runs as the logged-in user, never elevated. It talks to the
worker through the localhost JSON API (worker.local_api) and, when the
service is not running, reads the service state from Windows instead.
"""

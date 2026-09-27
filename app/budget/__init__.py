"""Budget and account state — reservations, usage reconciliation, ledger.

Milestone 2. This package owns:

  - account state (allowance, initial usage, completed usage, reservations)
  - atomic token reservations (SQLite transactions, no negative overspend)
  - usage reconciliation against actual upstream usage
  - locally estimated remaining monthly budget
  - failure exclusions (cooldown) and last-known per-minute rate-limit state

It never performs HTTP requests and never decides routing. The monthly budget
is a LOCAL ESTIMATE and is kept strictly separate from per-minute
``X-RateLimit-*`` header information.
"""

from __future__ import annotations

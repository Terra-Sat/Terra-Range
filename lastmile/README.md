# LastMile

This module implements the Terra Range last-mile delivery layer for registration, consent, and outbound messaging.

It is intentionally designed to run independently from the main platform and to depend on Terra Range only via the documented HTTP contract.

## Project status

- Contract-first development
- Python 3.11+
- FastAPI + SQLite local-first
- Fake provider and mock API for offline testing

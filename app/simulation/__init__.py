"""Slice 7: standalone simulation engine.

Generates synthetic drivers/riders, moves drivers, and fires ride requests
(including deliberately concentrated, barrier-synchronized bursts) against a
live running API -- the traffic source shown in CLAUDE.md section 5's
architecture diagram. Entrypoint: `python -m app.simulation`.
"""

"""Slice 7 entrypoint: `python -m app.simulation [options]`.

No scripts/ wrapper -- this is application code (app/simulation/), not an
operational script, consistent with scripts/ currently holding only
infrastructure bootstrapping (init-test-db.sql).
"""

from __future__ import annotations

import argparse
import logging

import httpx

from app.simulation.client import SimulationClient
from app.simulation.config import SimulationConfig
from app.simulation.reporting import format_summary, summarize
from app.simulation.runner import Simulation

logger = logging.getLogger(__name__)


def _build_arg_parser() -> argparse.ArgumentParser:
    defaults = SimulationConfig()
    parser = argparse.ArgumentParser(description="Ride-matching simulation engine (Slice 7).")
    parser.add_argument("--api-url", default=defaults.api_url)
    parser.add_argument("--num-drivers", type=int, default=defaults.num_drivers)
    parser.add_argument("--num-riders", type=int, default=defaults.num_riders)
    parser.add_argument("--hotspot-fraction", type=float, default=defaults.hotspot_fraction)
    parser.add_argument("--area-center-lat", type=float, default=defaults.area_center_lat)
    parser.add_argument("--area-center-lng", type=float, default=defaults.area_center_lng)
    parser.add_argument("--area-radius-km", type=float, default=defaults.area_radius_km)
    parser.add_argument(
        "--hotspot-radius-km",
        type=float,
        default=None,
        help="Defaults to a radius derived from h3_resolution/match_max_ring.",
    )
    parser.add_argument("--request-rate", type=float, default=defaults.request_rate)
    parser.add_argument("--burst-size", type=int, default=defaults.burst_size)
    parser.add_argument("--duration-seconds", type=float, default=defaults.duration_seconds)
    parser.add_argument("--driver-speed-kmh", type=float, default=defaults.driver_speed_kmh)
    parser.add_argument(
        "--movement-interval-seconds", type=float, default=defaults.movement_interval_seconds
    )
    parser.add_argument(
        "--ride-duration-min-seconds", type=float, default=defaults.ride_duration_min_seconds
    )
    parser.add_argument(
        "--ride-duration-max-seconds", type=float, default=defaults.ride_duration_max_seconds
    )
    parser.add_argument("--cancel-fraction", type=float, default=defaults.cancel_fraction)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--log-level", default="WARNING")
    return parser


def _config_from_args(args: argparse.Namespace) -> SimulationConfig:
    return SimulationConfig(
        api_url=args.api_url,
        num_drivers=args.num_drivers,
        num_riders=args.num_riders,
        hotspot_fraction=args.hotspot_fraction,
        area_center_lat=args.area_center_lat,
        area_center_lng=args.area_center_lng,
        area_radius_km=args.area_radius_km,
        hotspot_radius_km=args.hotspot_radius_km,
        request_rate=args.request_rate,
        burst_size=args.burst_size,
        duration_seconds=args.duration_seconds,
        driver_speed_kmh=args.driver_speed_kmh,
        movement_interval_seconds=args.movement_interval_seconds,
        ride_duration_min_seconds=args.ride_duration_min_seconds,
        ride_duration_max_seconds=args.ride_duration_max_seconds,
        cancel_fraction=args.cancel_fraction,
        seed=args.seed,
    )


def main() -> None:
    args = _build_arg_parser().parse_args()
    logging.basicConfig(level=args.log_level)

    config = _config_from_args(args)

    # Sized to burst_size so connection-pool exhaustion is a known, deliberate
    # ceiling rather than silently conflated with Redis lock contention when
    # reading results -- see docs/plans/slice-07-simulation-engine.md.
    limits = httpx.Limits(
        max_connections=config.burst_size * 4,
        max_keepalive_connections=config.burst_size * 2,
    )

    with httpx.Client(base_url=config.api_url, timeout=10.0, limits=limits) as http:
        client = SimulationClient(http)
        simulation = Simulation(client, config)
        outcomes = simulation.run()

        summary = summarize(outcomes)
        print(format_summary(summary))

        try:
            stats = client.get_stats()
            print("\n=== Server-side GET /stats (cross-check) ===")
            for key, value in stats.items():
                print(f"  {key}: {value}")
        except Exception:
            logger.warning("failed to fetch final /stats", exc_info=True)


if __name__ == "__main__":
    main()

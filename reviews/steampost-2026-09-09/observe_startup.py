"""Read-only startup observation for the recipient; does not remediate or launch."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from dayz_mcp.steam_preflight import WindowsSteamPreflightProvider, evaluate_steam_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=240.0)
    args = parser.parse_args()
    if not 0 < args.seconds <= 600:
        parser.error('--seconds must be in (0, 600]')
    provider = WindowsSteamPreflightProvider()
    deadline = time.monotonic() + args.seconds
    previous = None
    while True:
        session = evaluate_steam_session(provider)
        row = {
            'steam_registered_pid': session.steam_registered_pid,
            'steam_live_pids': session.steam_live_pids,
            'registry_passed': session.error_code is None,
            'startup_probe_pid': None,
            'startup_complete': None,
            'probe_error': None,
        }
        try:
            pids = tuple(provider.steam_process_pids())
            if len(pids) == 1:
                row['startup_probe_pid'] = pids[0]
                row['startup_complete'] = provider.steam_startup_complete(pids[0])
        except Exception as error:
            row['probe_error'] = type(error).__name__
        if row != previous:
            print(json.dumps({
                'observed_at': datetime.now().astimezone().isoformat(timespec='milliseconds'),
                **row,
            }), flush=True)
            previous = row
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 0
        time.sleep(min(0.2, remaining))


if __name__ == '__main__':
    raise SystemExit(main())

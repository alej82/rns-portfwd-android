#!/usr/bin/env python3
"""Headless RNS Portfwd engine harness.

Runs the mobile engine without Kivy against a private Reticulum shared
instance. Used by the integration tests and for desktop development.
"""

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hash", help="server destination hash (32 hex characters)")
    parser.add_argument("--forward", action="append", default=[], metavar="NAME:PORT:PASSWORD")
    parser.add_argument("--configdir", default=os.path.expanduser("~/.reticulum"))
    parser.add_argument("--statedir", default=None)
    parser.add_argument("--shared-port", type=int, default=37428)
    parser.add_argument("--control-port", type=int, default=37429)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--list-password", default="")
    parser.add_argument("--probe", metavar="SERVICE:PASSWORD", default=None)
    return parser.parse_args()


def parse_forward(value):
    name, port, password = value.split(":", 2)
    return {"name": name, "local_port": int(port), "password": password}


def parse_probe(value):
    service, password = value.split(":", 1)
    return service, password


def main():
    args = parse_args()
    statedir = args.statedir or os.path.join(args.configdir, "portfwd-headless")
    os.makedirs(statedir, exist_ok=True)

    from portfwd_android.engine import Engine
    from portfwd_android.validation import ValidationError

    engine = Engine(
        rns_dir=args.configdir,
        config_dir=statedir,
        shared_port=args.shared_port,
        control_port=args.control_port,
        require_shared=True,
    )
    engine.start()
    status = engine.snapshot()["transport"]
    if status.get("error"):
        print(f"transport error: {status['error']}", file=sys.stderr)
        engine.shutdown()
        return 2

    try:
        if args.list:
            engine.list_services(args.hash, args.list_password)
            return wait_aux(engine, "list", args.json)
        if args.probe:
            service, password = parse_probe(args.probe)
            engine.probe(args.hash, service, password)
            return wait_aux(engine, "probe", args.json)
        forwards = [parse_forward(value) for value in args.forward]
        engine.connect(args.hash, forwards, args.list_password or None)
    except ValidationError as error:
        print(f"invalid configuration: {error}", file=sys.stderr)
        engine.shutdown()
        return 2
    except Exception as error:
        print(f"connect error: {type(error).__name__}: {error}", file=sys.stderr)
        engine.shutdown()
        return 2

    started = time.monotonic()
    announced = False
    try:
        while True:
            for event in engine.drain_events():
                if args.json:
                    print(json.dumps(event, ensure_ascii=False, sort_keys=True), flush=True)
                elif event["type"] in ("error", "session_retry", "link_lost"):
                    print(f"event {event['seq']} {event['type']}: {json.dumps(event, ensure_ascii=False)}", flush=True)
                if event["type"] == "error":
                    return 2
            snapshot = engine.snapshot()
            state = snapshot["session"]["state"]
            if state == "listening" and not announced:
                announced = True
                if args.json:
                    print(json.dumps({"snapshot": snapshot}, ensure_ascii=False, sort_keys=True), flush=True)
                else:
                    for forward in snapshot["forwards"]:
                        print(
                            f"LISTEN {forward['name']} {forward['actual_port']}",
                            flush=True,
                        )
                    print("READY", flush=True)
            if state == "error":
                print(
                    f"session error: {snapshot['session'].get('error_code')} "
                    f"{snapshot['session'].get('error')}",
                    file=sys.stderr,
                )
                return 2
            if args.duration and time.monotonic() - started >= args.duration:
                return 0
            time.sleep(0.1)
    except KeyboardInterrupt:
        return 0
    finally:
        engine.shutdown()


def wait_aux(engine, kind, as_json):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        for event in engine.drain_events():
            if as_json:
                print(json.dumps(event, ensure_ascii=False, sort_keys=True), flush=True)
            if event["type"] == f"{kind}_result":
                engine.shutdown()
                return 0 if event.get("ok") else 2
        time.sleep(0.1)
    engine.shutdown()
    return 2


if __name__ == "__main__":
    sys.exit(main())

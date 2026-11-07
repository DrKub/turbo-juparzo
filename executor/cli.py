import json
import os
import signal
import sys
from collections.abc import Mapping
from pathlib import Path

from executor.config import ConfigError, load_config
from executor.lock import LockBusy
from executor.quote import UrllibTransport
from executor.run import StopFlag, run
from executor.startup import find_startup_refusal


def bound_state(inbox: Path, state: str | None) -> Path:
    inbox_absolute = Path(os.path.abspath(inbox))
    if state is None:
        return inbox_absolute.parent / "data" / "jupiter-dry-runs"
    return Path(os.path.abspath(state))


def install_sigterm(stop: StopFlag) -> None:
    def handler(signum, frame):
        stop.set()

    signal.signal(signal.SIGTERM, handler)


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ if environ is None else environ)
    refusal = find_startup_refusal(args, env)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 3
    values = {}
    for index in range(0, len(args), 2):
        values[args[index]] = args[index + 1]
    if "--config" not in values or "--inbox" not in values:
        print("usage", file=sys.stderr)
        return 1
    try:
        config = load_config(Path(values["--config"]))
    except (OSError, ConfigError, ValueError, json.JSONDecodeError):
        print("refused: config", file=sys.stderr)
        return 3
    state = bound_state(Path(values["--inbox"]), values.get("--state"))
    stop = StopFlag()
    install_sigterm(stop)
    try:
        return run(config, Path(values["--inbox"]), state, UrllibTransport(), stop=stop)
    except LockBusy:
        print("lock busy", file=sys.stderr)
        return 2

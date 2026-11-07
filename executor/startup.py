import re
from collections.abc import Mapping

_LIVE_FLAGS = {
    "--live",
    "--taker",
    "--payer",
    "--referral-account",
    "--api-key",
    "--execute-url",
    "--build-url",
    "--signer",
    "--rpc-url",
    "--webhook-url",
    "--private-key",
}
_MANDATE_FLAGS = {"--mandate", "--mandate-expiry"}
_ALLOWED = {"--config", "--inbox", "--state"}
_SECRET_NAME = re.compile(r"(PRIVATE|SECRET|API_KEY|APIKEY|PHANTOM|HELIUS|WEBHOOK|MNEMONIC|SEED)", re.I)
_DRY_MODES = {"dry_run", "dry-run"}
_LIVE_VALUES = {"1", "true", "yes", "on"}
_OFF_VALUES = {"0", "false", "no", "off"}
_SECRET_ENV = {
    "TAKER",
    "FEE_PAYER",
    "PAYER",
    "REFERRAL_ACCOUNT",
    "REFERRAL_FEE",
    "JUPITER_API_KEY",
    "X_API_KEY",
    "API_KEY",
    "EXECUTE_URL",
    "BUILD_URL",
    "SIGNER",
    "WALLET_SECRET",
    "PRIVATE_KEY",
    "SECRET_KEY",
    "RPC_URL",
    "HELIUS_URL",
    "WEBHOOK_URL",
    "WEBHOOK_AUTH",
}


def _lookup(env: Mapping[str, str], name: str):
    for key, value in env.items():
        if key.lower() == name.lower():
            return value
    return None


def find_startup_refusal(argv: list[str], env: Mapping[str, str]) -> str | None:
    names = set(argv)
    if names & _LIVE_FLAGS or any(item.startswith("--live=") for item in argv):
        return "refused: live"
    if names & _MANDATE_FLAGS or env.get("MANDATE_FILE") or env.get("MANDATE_EXPIRY"):
        return "refused: mandate"
    mode = _lookup(env, "MODE")
    live = _lookup(env, "LIVE")
    dry_run = _lookup(env, "DRY_RUN")
    if isinstance(mode, str) and mode.strip() and mode.strip().casefold() not in _DRY_MODES:
        return "refused: live"
    if isinstance(live, str) and live.strip().casefold() in _LIVE_VALUES:
        return "refused: live"
    if isinstance(dry_run, str) and dry_run.strip().casefold() in _OFF_VALUES:
        return "refused: live"
    if any(env.get(name) for name in _SECRET_ENV):
        return "refused: live"
    for key, value in env.items():
        if value and _SECRET_NAME.search(key):
            return "refused: live"
    index = 0
    while index < len(argv):
        flag = argv[index]
        if flag not in _ALLOWED:
            return "refused: config"
        if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
            return "refused: config"
        index += 2
    return None

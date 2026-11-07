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


def find_startup_refusal(argv: list[str], env: Mapping[str, str]) -> str | None:
    names = set(argv)
    if names & _LIVE_FLAGS or any(item.startswith("--live=") for item in argv):
        return "refused: live"
    if names & _MANDATE_FLAGS or env.get("MANDATE_FILE") or env.get("MANDATE_EXPIRY"):
        return "refused: mandate"
    if env.get("MODE") == "live" or env.get("LIVE") == "1" or env.get("DRY_RUN") in {"0", "false"}:
        return "refused: live"
    if any(env.get(name) for name in _SECRET_ENV):
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

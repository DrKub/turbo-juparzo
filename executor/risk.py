from executor.types import SOL_MINT, Book, DryRunConfig, Signal


def denylist_label(config: DryRunConfig) -> str:
    if config.denylist_mints is None:
        return "not_configured"
    return "configured"


def prequote(config: DryRunConfig, book: Book, signal: Signal) -> str | None:
    if signal.mint == SOL_MINT:
        return "pair"
    if config.denylist_mints is not None and signal.mint in config.denylist_mints:
        return "denylist"
    if signal.mint not in config.mint_metadata:
        return "unknown_mint"
    if signal.side == "buy":
        if signal.mint in book.positions:
            return "risk_mint_open"
        if len(book.positions) >= 3:
            return "risk_max_positions"
        if signal.id not in config.buy_estimates or signal.size_lamports is None:
            return "risk_reserve"
        if signal.size_lamports * 100 > book.bank_lamports * 8:
            return "risk_cap"
        estimate = config.buy_estimates[signal.id]
        post = (
            book.bank_lamports
            - signal.size_lamports
            - estimate.estimated_fee_lamports
            - estimate.estimated_rent_lamports
        )
        if post < 30_000_000:
            return "risk_reserve"
        return None
    if signal.mint not in book.positions:
        return "risk_no_position"
    if signal.token_amount is None or signal.token_amount != book.positions[signal.mint].token_base_units:
        return "risk_sell_size"
    return None

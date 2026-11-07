from executor.types import MarkRecord, Receipt, StoredQuote


def _quote(quote: StoredQuote | None) -> dict | None:
    if quote is None:
        return None
    return {
        "router": quote.router,
        "inAmount": quote.in_amount,
        "outAmount": quote.out_amount,
        "feeBps": quote.fee_bps,
        "feeMint": quote.fee_mint,
        "platformFee": quote.platform_fee,
    }


def _canon(value: int | None) -> str | None:
    if value is None:
        return None
    return str(value)


def receipt_payload(receipt: Receipt) -> dict:
    return {
        "record_type": "jupiter_dry_run_receipt",
        "id": receipt.id,
        "side": receipt.side,
        "status": "dry_run",
        "outcome": receipt.outcome,
        "trigger": receipt.trigger,
        "mint": receipt.mint,
        "quoted_in_amount": receipt.quoted_in_amount,
        "quoted_out_amount": receipt.quoted_out_amount,
        "reason": receipt.reason,
        "denylist": receipt.denylist,
        "seq": str(receipt.seq),
        "buy_lamports": _canon(receipt.buy_lamports),
        "estimated_fee_lamports": _canon(receipt.estimated_fee_lamports),
        "estimated_rent_lamports": _canon(receipt.estimated_rent_lamports),
        "quote": _quote(receipt.quote),
    }


def mark_payload(mark: MarkRecord) -> dict:
    return {
        "record_type": "jupiter_dry_run_mark",
        "buy_id": mark.buy_id,
        "n": str(mark.n),
        "mark_out_lamports": str(mark.mark_out_lamports),
        "mark_in_base_units": str(mark.mark_in_base_units),
        "seq": str(mark.seq),
        "quote": _quote(mark.quote),
    }

import sys

from executor.quote import UrllibTransport, classify, order_url
from executor.types import SMOKE_AMOUNT, SOL_MINT, USDC_MINT, QuoteRequest, QuoteSuccess


def smoke_request() -> QuoteRequest:
    return QuoteRequest(SOL_MINT, USDC_MINT, SMOKE_AMOUNT)


def main() -> int:
    request = smoke_request()
    try:
        status, body = UrllibTransport().get(order_url(request), {})
    except (TimeoutError, OSError, ValueError):
        print("smoke_unavailable", file=sys.stderr)
        return 1
    result = classify(request, status, body)
    if not isinstance(result, QuoteSuccess):
        print("smoke_unavailable", file=sys.stderr)
        return 1
    print(result.quote.router, result.quote.in_amount, result.quote.out_amount, result.quote.fee_bps)
    return 0


if __name__ == "__main__":
    sys.exit(main())

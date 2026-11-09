import json
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from executor.integers import parse_pos
from executor.types import ORDER_URL, QuoteRequest, QuoteSuccess, QuoteUnavailable, QuoteUnsafe, StoredQuote


class _RefuseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OSError("redirect refused")


def urlopen(req, timeout=10):
    # Keep the process environment proxy. Clearing it would bypass an operator
    # or infrastructure proxy. The application target stays the order URL, and
    # redirects are refused, so a proxy cannot retarget this process at /execute.
    opener = build_opener(_RefuseRedirect)
    return opener.open(req, timeout=timeout)


def order_url(request: QuoteRequest) -> str:
    if request.input_mint.startswith("http") or request.output_mint.startswith("http"):
        raise ValueError("mint is not a quote parameter")
    query = urlencode(
        (
            ("inputMint", request.input_mint),
            ("outputMint", request.output_mint),
            ("amount", request.amount),
        )
    )
    return ORDER_URL + "?" + query


def _platform_fee(body: dict) -> dict | None:
    raw = body.get("platformFee")
    if not isinstance(raw, dict):
        return None
    kept = {}
    if isinstance(raw.get("amount"), str):
        kept["amount"] = raw["amount"]
    fee_bps = raw.get("feeBps")
    if isinstance(fee_bps, int) and not isinstance(fee_bps, bool):
        kept["feeBps"] = fee_bps
    if isinstance(raw.get("feeMint"), str):
        kept["feeMint"] = raw["feeMint"]
    return kept or None


def classify(request: QuoteRequest, status: int, body: object):
    if not isinstance(body, dict):
        return QuoteUnavailable()
    if "transaction" in body and body["transaction"] is not None:
        return QuoteUnsafe()
    if status != 200 or "transaction" not in body:
        return QuoteUnavailable()
    if body.get("inputMint") != request.input_mint or body.get("outputMint") != request.output_mint:
        return QuoteUnavailable()
    if body.get("inAmount") != request.amount or parse_pos(body.get("outAmount")) is None:
        return QuoteUnavailable()
    fee_bps = body.get("feeBps")
    if isinstance(fee_bps, bool) or not isinstance(fee_bps, int):
        fee_bps = None
    fee_mint = body.get("feeMint") if isinstance(body.get("feeMint"), str) else None
    router = body.get("router") if isinstance(body.get("router"), str) else None
    return QuoteSuccess(
        StoredQuote(router, body["inAmount"], body["outAmount"], fee_bps, fee_mint, _platform_fee(body))
    )


def quote_once(transport, request: QuoteRequest):
    url = order_url(request)
    if not url.startswith(ORDER_URL + "?") or "/execute" in url or "/build" in url:
        return QuoteUnavailable()
    try:
        status, body = transport.get(url, {})
    except (TimeoutError, OSError, ValueError):
        return QuoteUnavailable()
    return classify(request, status, body)


class UrllibTransport:
    def get(self, url, headers):
        if not url.startswith(ORDER_URL + "?"):
            raise OSError("refused url")
        if any(str(key).lower() == "x-api-key" for key in headers):
            raise OSError("refused api key")
        req = Request(url, headers=dict(headers), method="GET")
        try:
            with urlopen(req, timeout=10) as response:
                raw = response.read()
                status = response.status
        except HTTPError as exc:
            raise OSError(f"quote http {exc.code}") from exc
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OSError("quote body is not json") from exc
        return status, body

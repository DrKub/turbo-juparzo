import unittest

from executor.quote import UrllibTransport, classify, order_url, quote_once
from executor.types import ORDER_URL, QuoteRequest, QuoteSuccess, QuoteUnavailable, QuoteUnsafe, SOL_MINT
from tests.fixtures import MINT_A


def request():
    return QuoteRequest(SOL_MINT, MINT_A, "80000000")


def ok_body(**extra):
    body = {
        "router": "metis",
        "inAmount": "80000000",
        "outAmount": "1000",
        "feeBps": 10,
        "feeMint": MINT_A,
        "platformFee": {"amount": "1", "feeBps": 10, "feeMint": MINT_A, "ignored": True},
        "transaction": None,
        "inputMint": SOL_MINT,
        "outputMint": MINT_A,
        "requestId": "do-not-store",
        "inUsdValue": 12,
        "routePlan": [{"drop": True}],
    }
    body.update(extra)
    return body


class FakeTransport:
    def __init__(self, status, body, error=None):
        self.status = status
        self.body = body
        self.error = error
        self.calls = []

    def get(self, url, headers):
        self.calls.append((url, dict(headers)))
        if self.error:
            raise self.error
        return self.status, self.body


class QuoteTest(unittest.TestCase):
    def test_url_has_three_params_and_no_taker(self):
        url = order_url(request())
        self.assertTrue(url.startswith(ORDER_URL + "?"))
        self.assertIn("inputMint=" + SOL_MINT, url)
        self.assertIn("outputMint=" + MINT_A, url)
        self.assertIn("amount=80000000", url)
        self.assertNotIn("taker", url)
        self.assertNotIn("execute", url)
        self.assertNotIn("api-key", url)

    def test_success_strips_transaction_and_usd(self):
        result = classify(request(), 200, ok_body())
        self.assertIsInstance(result, QuoteSuccess)
        self.assertEqual(result.quote.out_amount, "1000")
        self.assertEqual(result.quote.platform_fee, {"amount": "1", "feeBps": 10, "feeMint": MINT_A})

    def test_transaction_string_is_unsafe_and_transport_errors_commit_nothing(self):
        self.assertIsInstance(classify(request(), 200, ok_body(transaction="")), QuoteUnsafe)
        self.assertIsInstance(classify(request(), 200, ok_body(transaction="AQID")), QuoteUnsafe)
        self.assertIsInstance(classify(request(), 200, ok_body(inAmount="1")), QuoteUnavailable)
        self.assertIsInstance(classify(request(), 429, ok_body()), QuoteUnavailable)
        fake = FakeTransport(200, ok_body(), TimeoutError("slow"))
        self.assertIsInstance(quote_once(fake, request()), QuoteUnavailable)
        self.assertEqual(fake.calls[0][1], {})

    def test_missing_transaction_or_mint_echo_is_not_a_quote(self):
        spoof = {
            "router": "spoof",
            "inAmount": "80000000",
            "outAmount": "10",
        }
        self.assertIsInstance(classify(request(), 200, spoof), QuoteUnavailable)
        echoed = dict(spoof, transaction=None)
        self.assertIsInstance(classify(request(), 200, echoed), QuoteUnavailable)
        wrong = dict(echoed, inputMint=MINT_A, outputMint=SOL_MINT)
        self.assertIsInstance(classify(request(), 200, wrong), QuoteUnavailable)

    def test_urllib_transport_builds_a_get_without_api_key(self):
        captured = {}

        class Response:
            status = 200

            def read(self):
                return b'{"transaction":null,"inAmount":"80000000","outAmount":"1000"}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["headers"] = {key.lower(): value for key, value in req.header_items()}
            return Response()

        import executor.quote as quote_mod

        original = quote_mod.urlopen
        quote_mod.urlopen = fake_urlopen
        try:
            status, body = UrllibTransport().get(order_url(request()), {})
        finally:
            quote_mod.urlopen = original
        self.assertEqual(status, 200)
        self.assertEqual(body["outAmount"], "1000")
        self.assertNotIn("taker", captured["url"])
        self.assertNotIn("x-api-key", captured["headers"])

    def test_redirect_is_not_followed(self):
        import executor.quote as quote_mod

        class RedirectError(OSError):
            pass

        def fake_urlopen(req, timeout=0):
            raise RedirectError("redirect refused")

        original = quote_mod.urlopen
        quote_mod.urlopen = fake_urlopen
        try:
            with self.assertRaises(OSError):
                UrllibTransport().get(order_url(request()), {})
        finally:
            quote_mod.urlopen = original


if __name__ == "__main__":
    unittest.main()

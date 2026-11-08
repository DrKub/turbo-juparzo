# Offline Metis check

`./metis-offline` reads one serialized transaction and prints a JSON report. The launcher uses `.venv-metis-offline/bin/python` next to itself and sets `PYTHONPATH` to the repository root. It does not activate a shell, change the system interpreter, install packages, or contact the network.

From the repository root:

```bash
./metis-offline metis_offline/samples/legacy-unknown.json
```

The same request can be piped:

```bash
./metis-offline - < metis_offline/samples/legacy-unknown.json
```

Two input forms are accepted. A file or stdin whose first non-space character is not `{` is the standard-base64 serialization itself, including a transaction that already contains signature bytes. Whitespace inside that text is ignored. A JSON object is the other form:

- `transaction_b64`: standard base64. The committed sample is synthetic, with a zero signature and a zero blockhash. It is not a transaction from a network.
- `user_hex`, `input_mint_hex`, `output_mint_hex`: 32-byte values as hex. The sample uses repeated bytes, not a wallet or mint. Raw base64 mode does not take these fields and does not invent them.
- `amount`: integer. Raw base64 mode does not read an amount from the instruction data.
- `slippage_bps`: an integer or `null`. Neither is an approved slippage.
- `captured_tables_hex`: optional map of table id hex to address-byte hex. If you do not supply the table bytes, a lookup stays unresolved. The tool does not guess table addresses.

To check a transaction you already hold, write only its base64 text into a local file that is not part of git, then pass that path. Do not commit it, paste it into a pull request, or copy it into a log. A signed serialization is sensitive: anyone who has those bytes may be able to broadcast them. This repository has no saved real transaction, and no real sample was run.

Stdout is a JSON object and does not repeat the input bytes. `structural_decode` is true only when the wire parsed. `signatures_all_zero` says whether every signature slot is zero. `signatures_authentic` is false even when slots are non-zero: the bytes were not checked as a genuine signature. `economics_verified` is false: amounts, slippage, and fees were not derived from the instruction data. `instruction_semantics` is `unverified`. `kind` is `rejected`. `trade_authorized` is false. A parsed message is not a safe swap.

Exit codes:

- `0` is only `--help`.
- `1` means the launcher or the request failed before a check: missing environment, missing file, empty input, input above 1048576 characters, or a `{` document that is not valid JSON. The oversized or empty body is not printed.
- `2` means the check ran and the transaction is rejected. That includes malformed base64, a truncated message, an unsupported version, an unresolved lookup table, and an unknown program. The synthetic sample exits `2`. A non-zero signature does not change this outcome.

If `.venv-metis-offline/bin/python` is missing, the command exits `1` and prints the setup lines. Create that environment separately. For an offline machine, install the pins in `requirements-metis-offline.txt` from a local wheelhouse:

```bash
python3 -m venv .venv-metis-offline
.venv-metis-offline/bin/pip install --no-index --find-links WHEELHOUSE -r requirements-metis-offline.txt
```

The pins are `solders==0.29.0`, `jsonalias==0.1.1`, and `typing-extensions==4.16.0`. Fetching those packages is a prerequisite, not part of `./metis-offline`.

The command does not call Jupiter, an RPC, or a wallet. It does not sign or broadcast. Instruction data is not decoded as a swap. A parsed message can still be `unknown_program`, `slippage_unapproved`, or another rejection.

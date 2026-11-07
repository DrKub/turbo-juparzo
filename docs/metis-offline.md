# Offline Metis check

`./metis-offline` reads one synthetic transaction request and prints a JSON report. The launcher uses `.venv-metis-offline/bin/python` next to itself and sets `PYTHONPATH` to the repository root. It does not activate a shell, change the system interpreter, install packages, or contact the network.

From the repository root:

```bash
./metis-offline metis_offline/samples/legacy-unknown.json
```

The same request can be piped:

```bash
./metis-offline - < metis_offline/samples/legacy-unknown.json
```

The input is a JSON object:

- `transaction_b64`: standard base64 of an unsigned transaction. The sample is a synthetic legacy message with a zero signature and a zero blockhash.
- `user_hex`, `input_mint_hex`, `output_mint_hex`: 32-byte values as hex. The sample uses repeated bytes, not a wallet or mint.
- `amount`: integer.
- `slippage_bps`: an integer or `null`. Neither is an approved slippage.
- `captured_tables_hex`: optional map of table id hex to address-byte hex.

Stdout is a JSON object. `structural_decode` is true only when the wire parsed. `instruction_semantics` is `unverified`. `kind` is `rejected`. `trade_authorized` is false. This is not a safety approval.

Exit codes:

- `0` is only `--help`.
- `1` means the launcher or the input failed: missing environment, missing file, or invalid JSON. Nothing was evaluated.
- `2` means the check ran and the transaction is rejected. The sample exits `2`.

If `.venv-metis-offline/bin/python` is missing, the command exits `1` and prints the setup lines. Create that environment separately. For an offline machine, install the pins in `requirements-metis-offline.txt` from a local wheelhouse:

```bash
python3 -m venv .venv-metis-offline
.venv-metis-offline/bin/pip install --no-index --find-links WHEELHOUSE -r requirements-metis-offline.txt
```

The pins are `solders==0.29.0`, `jsonalias==0.1.1`, and `typing-extensions==4.16.0`. Fetching those packages is a prerequisite, not part of `./metis-offline`.

The command does not call Jupiter, an RPC, or a wallet. It does not sign or broadcast. Instruction data is not decoded as a swap. A parsed message can still be `unknown_program`, `slippage_unapproved`, or another rejection.

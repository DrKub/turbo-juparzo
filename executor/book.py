from executor.journal import JournalError
from executor.marks import apply_observation
from executor.types import Book, MarkRecord, Position, Receipt


def fold(starting_bank: int, receipts: list[Receipt], marks: list[MarkRecord]) -> Book:
    bank = starting_bank
    positions: dict[str, Position] = {}
    for receipt in sorted(receipts, key=lambda item: item.seq):
        if receipt.outcome != "quoted":
            continue
        if receipt.side == "buy":
            if (
                receipt.buy_lamports is None
                or receipt.estimated_fee_lamports is None
                or receipt.estimated_rent_lamports is None
                or receipt.quote is None
            ):
                raise ValueError("quoted buy is missing its cost")
            if receipt.mint in positions:
                raise JournalError("journal corrupt")
            out_units = int(receipt.quote.out_amount)
            bank -= receipt.buy_lamports + receipt.estimated_fee_lamports + receipt.estimated_rent_lamports
            positions[receipt.mint] = Position(
                receipt.id,
                receipt.mint,
                receipt.buy_lamports,
                out_units,
                out_units,
                receipt.buy_lamports,
                out_units,
                False,
            )
        else:
            position = positions.get(receipt.mint)
            if receipt.quote is None or position is None:
                raise JournalError("journal corrupt")
            if receipt.quote.in_amount != str(position.token_base_units):
                raise JournalError("journal corrupt")
            bank += int(receipt.quote.out_amount)
            positions.pop(receipt.mint)
    for mark in sorted(marks, key=lambda item: (item.buy_id, item.n)):
        for mint, position in list(positions.items()):
            if position.buy_id != mark.buy_id:
                continue
            positions[mint] = apply_observation(position, mark.mark_out_lamports, mark.mark_in_base_units)
    return Book(bank, positions)

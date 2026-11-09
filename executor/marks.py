from dataclasses import replace

from executor.types import Position


def decide_mark(position: Position, mark_out: int, mark_in: int) -> str:
    if mark_out * position.entry_out_base_units * 100 <= position.entry_buy_lamports * mark_in * 94:
        return "stop"
    if position.armed and mark_out * position.high_in_base_units * 100 <= position.high_out_lamports * mark_in * 94:
        return "trailing"
    return "observe"


def apply_observation(position: Position, mark_out: int, mark_in: int) -> Position:
    high_out = position.high_out_lamports
    high_in = position.high_in_base_units
    if mark_out * high_in > high_out * mark_in:
        high_out = mark_out
        high_in = mark_in
    armed = position.armed or (
        mark_out * position.entry_out_base_units * 100 >= position.entry_buy_lamports * mark_in * 112
    )
    return replace(position, high_out_lamports=high_out, high_in_base_units=high_in, armed=armed)

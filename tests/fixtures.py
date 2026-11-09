MINT_A = "8qbHbw2BbbTHBW1sbeqakYXVKRQM8Ne7pLK7m6CVfeR"
MINT_B = "CktRuQ2mttgRGkXJtyksdKHjUdc2C4TgDzyB98oEzy8"
MINT_C = "GgBaCs3NCBuZN12kCJgAW63ydqohFkHEdfdEXBPzLHq"
MINT_D = "LbUiWL3xVV8hTFYBVdbTNrpDo41NKS6o3LHHuDzjfcY"
MINT_DENIED = "cGfHiC6Kgg3FpFZvgwGcswsCRtp4aBP2fzuXRQPizuN"


def sample_config(**overrides) -> dict:
    body = {
        "bank_lamports": "1000000000",
        "mint_metadata": [
            {"mint": MINT_A, "decimals": 6},
            {"mint": MINT_B, "decimals": 6},
            {"mint": MINT_C, "decimals": 6},
            {"mint": MINT_D, "decimals": 6},
        ],
    }
    body.update(overrides)
    return body

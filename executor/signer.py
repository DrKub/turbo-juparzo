class SignerRefusal(Exception):
    pass


def sign(payload: bytes) -> bytes:
    raise SignerRefusal("signer refused")

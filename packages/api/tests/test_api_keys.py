"""Key generation/hashing — no DB, no Redis."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.security.api_keys import generate_api_key, hash_api_key, KEY_PREFIX_STR


def test_generated_key_shape():
    plaintext, key_hash, prefix = generate_api_key()
    assert plaintext.startswith(KEY_PREFIX_STR)
    assert key_hash == hash_api_key(plaintext)
    assert prefix == plaintext[:20]
    assert len(key_hash) == 64            # sha256 hex


def test_keys_are_unique():
    a, _, _ = generate_api_key()
    b, _, _ = generate_api_key()
    assert a != b


def test_hash_is_deterministic_and_one_way():
    p, h, _ = generate_api_key()
    assert hash_api_key(p) == h
    assert p not in h                     # the plaintext is not recoverable from the hash

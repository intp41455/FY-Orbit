"""Password hashing (argon2id) for the local user system.

Verification is constant-time on mismatch and treats malformed stored hashes
(e.g. the ``$locked$`` marker on the bootstrap user) as a plain failure — never
as an error leak.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError):
        return False

"""
blockchain/siwe.py — Sign-In With Ethereum (EIP-191 personal_sign).

Implements the SIWE challenge/verify pattern so a MetaMask user can prove
ownership of an Ethereum address and link it to their Sim.

Flow:
  1. Client sends their 0x address to POST /chain/challenge
     → Server stores a time-limited nonce, returns a human-readable message.
  2. Client passes the message to MetaMask:
         await window.ethereum.request({
             method: 'personal_sign',
             params: [message, address],
         });
     MetaMask shows the message in plain text — no hex blobs.
  3. Client sends address + signature to POST /chain/verify
     → Server recovers the signer, checks nonce, marks nonce used.
     → Returns the linked sim_id and a session token.

Nonces expire after NONCE_TTL_SECONDS and are single-use (replay protection).
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from eth_account import Account
from eth_account.messages import encode_defunct

from blockchain.eip712 import CHAIN_ID, CHAIN_NAME

NONCE_TTL_SECONDS = 120   # nonce valid for 2 minutes


# ── Nonce store ───────────────────────────────────────────────────────────────

@dataclass
class _NonceRecord:
    nonce:      str
    address:    str
    issued_at:  float = field(default_factory=time.time)
    used:       bool  = False

    @property
    def expired(self) -> bool:
        return time.time() - self.issued_at > NONCE_TTL_SECONDS


_nonces: dict[str, _NonceRecord] = {}   # nonce → record


def _purge_expired() -> None:
    expired = [k for k, v in _nonces.items() if v.expired]
    for k in expired:
        del _nonces[k]


# ── Challenge ─────────────────────────────────────────────────────────────────

def create_challenge(address: str, domain: str = "simchain.game") -> dict:
    """
    Create a SIWE challenge for `address`.

    Returns:
        {nonce, message, expires_at}

    The `message` is the exact string the frontend must pass to MetaMask's
    personal_sign. It is human-readable so MetaMask displays it clearly.
    """
    _purge_expired()

    nonce     = secrets.token_hex(8)
    issued_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    message = (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{address}\n"
        f"\n"
        f"Sign in to {CHAIN_NAME} to link your wallet to your Sim.\n"
        f"\n"
        f"URI: https://{domain}\n"
        f"Version: 1\n"
        f"Chain ID: {CHAIN_ID}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at}"
    )

    _nonces[nonce] = _NonceRecord(nonce=nonce, address=address.lower())

    return {
        "nonce":      nonce,
        "message":    message,
        "expires_at": time.time() + NONCE_TTL_SECONDS,
    }


# ── Verification ──────────────────────────────────────────────────────────────

def verify_signature(message: str, signature: str) -> str:
    """
    Recover the Ethereum address that signed `message` (personal_sign / EIP-191).

    Returns the checksum 0x address. Raises ValueError on failure.
    """
    try:
        msg_encoded = encode_defunct(text=message)
        sig_bytes   = bytes.fromhex(signature.removeprefix("0x"))
        recovered   = Account.recover_message(msg_encoded, signature=sig_bytes)
        return recovered
    except Exception as exc:
        raise ValueError(f"Signature recovery failed: {exc}") from exc


def verify_challenge(nonce: str, address: str, signature: str, message: str) -> str:
    """
    Full SIWE verification:
      1. Nonce exists, not expired, not used.
      2. Nonce was issued to this address.
      3. Recovered signer matches address.

    Returns the checksum address on success. Raises ValueError on any failure.
    """
    _purge_expired()

    record = _nonces.get(nonce)
    if record is None:
        raise ValueError("Unknown or expired nonce")
    if record.used:
        raise ValueError("Nonce already used (replay attack)")
    if record.expired:
        raise ValueError("Nonce expired")
    if record.address != address.lower():
        raise ValueError("Nonce was not issued to this address")

    recovered = verify_signature(message, signature)

    if recovered.lower() != address.lower():
        raise ValueError(
            f"Signature mismatch: expected {address[:12]}, got {recovered[:12]}"
        )

    record.used = True
    return recovered

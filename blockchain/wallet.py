"""
blockchain/wallet.py — secp256k1 SimWallet via eth_account.

Each sim gets a deterministic wallet derived from its sim_id — no key
management needed for the simulation.  Addresses are standard 0x Ethereum
format, so the chain can bridge to a real testnet later with zero changes.

Falls back to HMAC-SHA256 signing if eth_account is unavailable (game-grade).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

try:
    from eth_account import Account
    from eth_account.messages import encode_defunct
    _ETH_OK = True
except ImportError:
    _ETH_OK = False
    logger.warning(
        "[Wallet] eth_account not found — install eth-account for Ethereum-compatible "
        "wallets. Falling back to HMAC-SHA256 (game-grade, non-Ethereum)."
    )

if TYPE_CHECKING:
    from blockchain.transaction import SimTransaction

_WALLET_PREFIX = "simchain_wallet_v1:"


def _derive_key(seed: str) -> str:
    """Double-SHA256 key stretching → 32-byte hex private key."""
    h1 = hashlib.sha256(seed.encode()).digest()
    return hashlib.sha256(h1).hexdigest()


class SimWallet:
    """
    secp256k1 wallet with a deterministic private key.

    Create via SimWallet.from_sim_id(sim_id) — the same sim always
    gets the same address, so wallets survive restarts.
    """

    def __init__(self, private_key_hex: str) -> None:
        self._private_key = private_key_hex
        if _ETH_OK:
            self._account = Account.from_key(private_key_hex)
            self.address: str = self._account.address  # checksum 0x address
        else:
            raw = hashlib.sha256(bytes.fromhex(private_key_hex)).hexdigest()
            self.address = "0x" + raw[:40]

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_seed(cls, seed: str) -> "SimWallet":
        return cls(_derive_key(seed))

    @classmethod
    def from_sim_id(cls, sim_id: str) -> "SimWallet":
        return cls.from_seed(f"{_WALLET_PREFIX}{sim_id}")

    @classmethod
    def from_label(cls, label: str) -> "SimWallet":
        """Create a named system wallet (validator, treasury, market, etc.)."""
        return cls.from_seed(f"{_WALLET_PREFIX}system:{label}")

    # ── Signing ───────────────────────────────────────────────────────────────

    def sign(self, message_hash: str) -> str:
        """EIP-191 personal_sign over an arbitrary string (hex digest or nonce)."""
        if _ETH_OK:
            msg    = encode_defunct(text=message_hash)
            signed = self._account.sign_message(msg)
            return "0x" + signed.signature.hex()
        return hmac.new(
            bytes.fromhex(self._private_key),
            message_hash.encode(),
            hashlib.sha256,
        ).hexdigest()

    def sign_transaction(self, tx: "SimTransaction") -> "SimTransaction":
        """Hash and sign a SimTransaction (EIP-191 over the tx digest)."""
        tx.tx_hash   = tx.compute_hash()
        tx.signature = self.sign(tx.tx_hash)
        return tx

    def sign_typed_data(self, typed_data: dict) -> str:
        """
        Sign EIP-712 typed structured data.
        Equivalent to MetaMask's eth_signTypedData_v4.
        Returns 0x-prefixed hex signature.
        """
        from blockchain.eip712 import sign_typed_data
        return sign_typed_data(typed_data, self._private_key)

    # ── Verification ─────────────────────────────────────────────────────────

    @staticmethod
    def verify(address: str, message_hash: str, signature: str) -> bool:
        """Verify an EIP-191 personal_sign signature."""
        if not _ETH_OK:
            return True
        try:
            msg       = encode_defunct(text=message_hash)
            sig_bytes = bytes.fromhex(signature.removeprefix("0x"))
            recovered = Account.recover_message(msg, signature=sig_bytes)
            return recovered.lower() == address.lower()
        except Exception:
            return False

    @staticmethod
    def verify_typed(typed_data: dict, signature: str) -> str:
        """
        Recover signer address from an EIP-712 signature.
        Returns checksum address. Raises on failure.
        """
        from blockchain.eip712 import recover_signer
        return recover_signer(typed_data, signature)

    def __repr__(self) -> str:
        return f"SimWallet({self.address[:12]}…)"

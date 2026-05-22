"""
blockchain/eip712.py — EIP-712 typed structured data for SimChain.

Defines the domain separator and per-transaction-type structs so MetaMask
can present human-readable signing prompts instead of raw hex blobs.

MetaMask flow:
  1. Client calls eth_signTypedData_v4 with the typed data from build_*().
  2. Server calls recover_signer(typed_data, signature) to get the 0x address.
  3. That address is authoritative — no private key needed server-side.

Chain ID: 13371  (SimChain — unique, not in use on mainnet)
"""
from __future__ import annotations

import json
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data

CHAIN_ID   = 13371
CHAIN_NAME = "SimChain"

# ── EIP-712 domain ────────────────────────────────────────────────────────────

_DOMAIN_TYPES = [
    {"name": "name",    "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
]

_DOMAIN = {
    "name":    CHAIN_NAME,
    "version": "1",
    "chainId": CHAIN_ID,
}

# ── Typed structs ─────────────────────────────────────────────────────────────

_STRUCTS: dict[str, list[dict]] = {
    "SimTransfer": [
        {"name": "from",   "type": "address"},
        {"name": "to",     "type": "address"},
        {"name": "amount", "type": "uint256"},
        {"name": "nonce",  "type": "uint256"},
    ],
    "SimShopPurchase": [
        {"name": "buyer",     "type": "address"},
        {"name": "shopName",  "type": "string"},
        {"name": "item",      "type": "string"},
        {"name": "price",     "type": "uint256"},
        {"name": "nonce",     "type": "uint256"},
    ],
    "SimStockOrder": [
        {"name": "trader",  "type": "address"},
        {"name": "ticker",  "type": "string"},
        {"name": "shares",  "type": "uint256"},
        {"name": "side",    "type": "string"},    # "buy" | "sell"
        {"name": "nonce",   "type": "uint256"},
    ],
    "SimAgreement": [
        {"name": "partyA",         "type": "address"},
        {"name": "partyB",         "type": "address"},
        {"name": "agreementType",  "type": "string"},
        {"name": "amount",         "type": "uint256"},
        {"name": "durationTicks",  "type": "uint256"},
        {"name": "nonce",          "type": "uint256"},
    ],
    "SimLogin": [
        {"name": "address",   "type": "address"},
        {"name": "simId",     "type": "string"},
        {"name": "nonce",     "type": "string"},
        {"name": "issuedAt",  "type": "string"},
    ],
}

# ── Builders ──────────────────────────────────────────────────────────────────

def _build(primary_type: str, message: dict) -> dict:
    return {
        "types": {
            "EIP712Domain": _DOMAIN_TYPES,
            primary_type:   _STRUCTS[primary_type],
        },
        "primaryType": primary_type,
        "domain":      _DOMAIN,
        "message":     message,
    }


def build_transfer(from_addr: str, to_addr: str, amount_wei: int, nonce: int) -> dict:
    return _build("SimTransfer", {
        "from":   from_addr,
        "to":     to_addr,
        "amount": amount_wei,
        "nonce":  nonce,
    })


def build_shop_purchase(
    buyer_addr: str, shop_name: str, item: str, price_wei: int, nonce: int
) -> dict:
    return _build("SimShopPurchase", {
        "buyer":    buyer_addr,
        "shopName": shop_name,
        "item":     item,
        "price":    price_wei,
        "nonce":    nonce,
    })


def build_stock_order(
    trader_addr: str, ticker: str, shares: int, side: str, nonce: int
) -> dict:
    return _build("SimStockOrder", {
        "trader": trader_addr,
        "ticker": ticker,
        "shares": shares,
        "side":   side,
        "nonce":  nonce,
    })


def build_agreement(
    party_a: str,
    party_b: str,
    agreement_type: str,
    amount_wei: int,
    duration_ticks: int,
    nonce: int,
) -> dict:
    return _build("SimAgreement", {
        "partyA":        party_a,
        "partyB":        party_b,
        "agreementType": agreement_type,
        "amount":        amount_wei,
        "durationTicks": duration_ticks,
        "nonce":         nonce,
    })


def build_login(address: str, sim_id: str, nonce: str, issued_at: str) -> dict:
    return _build("SimLogin", {
        "address":  address,
        "simId":    sim_id,
        "nonce":    nonce,
        "issuedAt": issued_at,
    })

# ── Verification ──────────────────────────────────────────────────────────────

def recover_signer(typed_data: dict, signature: str) -> str:
    """
    Recover the address that produced `signature` over EIP-712 `typed_data`.
    Returns the checksum 0x address.  Raises on invalid input.

    This is the server-side counterpart to MetaMask's eth_signTypedData_v4.
    """
    encoded  = encode_typed_data(full_message=typed_data)
    sig_bytes = signature if isinstance(signature, bytes) else bytes.fromhex(
        signature.removeprefix("0x")
    )
    return Account.recover_message(encoded, signature=sig_bytes)


def sign_typed_data(typed_data: dict, private_key: str) -> str:
    """Sign typed data with a server-side key. Returns 0x-prefixed hex signature."""
    encoded = encode_typed_data(full_message=typed_data)
    signed  = Account.sign_message(encoded, private_key=private_key)
    return "0x" + signed.signature.hex()


# ── MetaMask add_chain params ─────────────────────────────────────────────────

def metamask_add_chain_params(rpc_url: str) -> dict:
    """
    Return the params object for MetaMask's wallet_addEthereumChain RPC call.
    Pass this from your frontend to window.ethereum.request(...).
    """
    return {
        "chainId":         hex(CHAIN_ID),
        "chainName":       CHAIN_NAME,
        "rpcUrls":         [rpc_url],
        "nativeCurrency":  {
            "name":     "SimCoin",
            "symbol":   "SIM",
            "decimals": 18,
        },
        "blockExplorerUrls": [],   # no public explorer yet
    }

"""
blockchain/rpc.py — Ethereum JSON-RPC 2.0 handler for SimChain.

Implements the minimal subset MetaMask needs to treat SimChain as a
custom network:

  eth_chainId           — 0x<CHAIN_ID hex>
  net_version           — CHAIN_ID as decimal string
  eth_blockNumber       — 0x<height hex>
  eth_getBalance        — 0x<wei hex>  (SimCoin balance)
  eth_gasPrice          — 0x0          (no gas in PoA)
  eth_estimateGas       — 0x5208       (21000 standard)
  eth_accounts          — []           (server holds no hot wallets)
  eth_call              — limited: ERC-20 balanceOf + symbol + decimals
  net_listening         — true

MetaMask also issues these — we silently accept:
  eth_getTransactionCount   → nonce for the address
  eth_getBlockByNumber      → stub block object
  eth_getTransactionReceipt → stub receipt

Unsupported methods return a JSON-RPC error; MetaMask gracefully degrades.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from blockchain.eip712 import CHAIN_ID

if TYPE_CHECKING:
    from blockchain.chain import SimChain

# ── ABI selector constants for eth_call dispatch ──────────────────────────────
# keccak256("balanceOf(address)")[:4]  = 0x70a08231
# keccak256("symbol()")[:4]            = 0x95d89b41
# keccak256("decimals()")[:4]          = 0x313ce567
# keccak256("totalSupply()")[:4]       = 0x18160ddd
_SEL_BALANCE_OF   = "70a08231"
_SEL_SYMBOL       = "95d89b41"
_SEL_DECIMALS     = "313ce567"
_SEL_TOTAL_SUPPLY = "18160ddd"


def _hex(n: int) -> str:
    return hex(n)


def _pad32(value: int) -> str:
    return hex(value)[2:].zfill(64)


def _decode_address_from_calldata(data: str) -> str:
    """Extract the address argument from ABI-encoded call data (32-byte padded)."""
    raw = data.removeprefix("0x")
    if len(raw) < 8 + 64:
        return "0x" + "0" * 40
    addr_hex = raw[8 + 24 : 8 + 64]   # skip selector (4B) + 12B padding
    return "0x" + addr_hex


# ── Handler ───────────────────────────────────────────────────────────────────

def handle_rpc(method: str, params: list, chain: "SimChain") -> Any:
    """
    Dispatch an Ethereum JSON-RPC method and return the raw result value.
    Raises KeyError for unsupported methods so the caller can return an error.
    """

    # ── Network / chain identity ──────────────────────────────────────────────
    if method == "eth_chainId":
        return _hex(CHAIN_ID)

    if method == "net_version":
        return str(CHAIN_ID)

    if method == "net_listening":
        return True

    # ── Chain state ───────────────────────────────────────────────────────────
    if method == "eth_blockNumber":
        return _hex(chain.height)

    if method == "eth_gasPrice":
        return "0x0"    # PoA: no gas fees

    if method == "eth_estimateGas":
        return "0x5208"  # 21 000 — standard transfer gas

    if method == "eth_accounts":
        return []

    # ── Address queries ───────────────────────────────────────────────────────
    if method == "eth_getBalance":
        address = params[0] if params else "0x0"
        bal     = chain.balance_of(address)
        return _hex(bal)

    if method == "eth_getTransactionCount":
        address = params[0] if params else "0x0"
        nonce   = chain._nonces.get(address.lower(), 0)
        return _hex(nonce)

    # ── Block stubs (MetaMask may poll these) ─────────────────────────────────
    if method in ("eth_getBlockByNumber", "eth_getBlockByHash"):
        block_ref = params[0] if params else "latest"
        idx = chain.height if block_ref in ("latest", "pending") else (
            int(block_ref, 16) if isinstance(block_ref, str) and block_ref.startswith("0x")
            else chain.height
        )
        blk = chain.get_block(idx)
        if blk is None:
            return None
        return {
            "number":           _hex(blk.index),
            "hash":             "0x" + blk.block_hash,
            "parentHash":       "0x" + blk.prev_hash,
            "timestamp":        _hex(int(blk.timestamp)),
            "transactions":     [
                "0x" + tx.get("tx_hash", "0" * 64)
                for tx in blk.transactions
            ],
            "gasLimit":         "0xffffffffffffffff",
            "gasUsed":          "0x0",
            "miner":            blk.validator,
            "difficulty":       "0x0",
            "totalDifficulty":  "0x0",
            "extraData":        "0x",
            "nonce":            "0x0000000000000000",
            "size":             _hex(len(blk.transactions) * 256),
            "transactionCount": _hex(len(blk.transactions)),
        }

    if method == "eth_getTransactionReceipt":
        tx_hash = params[0] if params else "0x0"
        # Stub — all SimChain txs are considered confirmed at their block
        return {
            "transactionHash":  tx_hash,
            "blockNumber":      _hex(chain.height),
            "blockHash":        "0x" + chain.head.block_hash,
            "status":           "0x1",
            "gasUsed":          "0x5208",
            "cumulativeGasUsed":"0x5208",
            "logs":             [],
            "from":             "0x" + "0" * 40,
            "to":               "0x" + "0" * 40,
        }

    # ── ERC-20 read-only calls (so MetaMask can show $SIM balance in wallet) ──
    if method == "eth_call":
        call   = params[0] if params else {}
        data   = call.get("data", "0x")
        sel    = data.removeprefix("0x")[:8].lower()

        if sel == _SEL_BALANCE_OF:
            addr = _decode_address_from_calldata(data)
            bal  = chain.balance_of(addr)
            return "0x" + _pad32(bal)

        if sel == _SEL_SYMBOL:
            # ABI-encoded string "SIM"
            return (
                "0x"
                + _pad32(32)          # offset
                + _pad32(3)           # length
                + "53494d" + "0" * 58 # "SIM" + padding
            )

        if sel == _SEL_DECIMALS:
            return "0x" + _pad32(18)

        if sel == _SEL_TOTAL_SUPPLY:
            simcoin = chain.get_contract("simcoin")
            supply  = simcoin._total_supply if simcoin else 0
            return "0x" + _pad32(supply)

        return "0x"  # unsupported call → empty return

    raise KeyError(f"Unsupported RPC method: {method}")


# ── JSON-RPC 2.0 envelope ─────────────────────────────────────────────────────

def json_rpc_response(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def json_rpc_error(req_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id":      req_id,
        "error":   {"code": code, "message": message},
    }


def dispatch(body: dict, chain: "SimChain") -> dict:
    """
    Full JSON-RPC 2.0 dispatch.  Pass the parsed request body, get back
    the complete response dict (ready to serialize as JSON).
    """
    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", [])

    try:
        result = handle_rpc(method, params, chain)
        return json_rpc_response(req_id, result)
    except KeyError as exc:
        return json_rpc_error(req_id, -32601, str(exc))
    except Exception as exc:
        return json_rpc_error(req_id, -32603, f"Internal error: {exc}")

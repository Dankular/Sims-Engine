"""blockchain/contracts/base.py — SmartContract base class."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from blockchain.chain import SimChain
    from blockchain.transaction import SimTransaction


class SmartContract:
    """
    Base class for all SimChain contracts.

    Subclasses implement:
      on_<tx_type>(tx, chain)  — called when a matching tx is executed
      _method_<name>(tx, args, chain) — called via TX_CONTRACT dispatch

    contract_id must be a unique string — it doubles as the contract's
    on-chain address (e.g. "contract:simcoin").
    """

    contract_id: str = "base"

    def execute(
        self,
        method: str,
        tx: "SimTransaction",
        args: dict,
        chain: "SimChain",
    ) -> Any:
        fn = getattr(self, f"_method_{method}", None)
        if fn is None:
            raise NotImplementedError(f"{type(self).__name__} has no method '{method}'")
        return fn(tx, args, chain)

    def address(self) -> str:
        return f"contract:{self.contract_id}"

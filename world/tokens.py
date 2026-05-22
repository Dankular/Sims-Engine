from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ItemToken:
    token_id: str
    owner_id: str
    item_ref: str
    rarity: str
    minted_tick: int


class TokenEconomy:
    def __init__(self) -> None:
        self._simcoin: dict[str, float] = {}
        self._items: dict[str, ItemToken] = {}
        self._listings: dict[str, dict] = {}
        self._nonce = 0

    def ensure_wallet(self, sim_id: str, initial: float = 0.0) -> None:
        if sim_id not in self._simcoin:
            self._simcoin[sim_id] = max(0.0, float(initial))

    def mint_simcoin(self, sim_id: str, amount: float) -> None:
        self.ensure_wallet(sim_id)
        self._simcoin[sim_id] += max(0.0, float(amount))

    def transfer_simcoin(self, from_id: str, to_id: str, amount: float) -> bool:
        self.ensure_wallet(from_id)
        self.ensure_wallet(to_id)
        amt = max(0.0, float(amount))
        if self._simcoin[from_id] < amt:
            return False
        self._simcoin[from_id] -= amt
        self._simcoin[to_id] += amt
        return True

    def mint_item_token(
        self, owner_id: str, item_ref: str, rarity: str, tick: int
    ) -> str:
        self._nonce += 1
        tid = f"nft_{self._nonce:07d}"
        self._items[tid] = ItemToken(
            token_id=tid,
            owner_id=owner_id,
            item_ref=item_ref,
            rarity=rarity,
            minted_tick=int(tick),
        )
        return tid

    def transfer_item_token(self, token_id: str, to_owner_id: str) -> bool:
        tok = self._items.get(token_id)
        if not tok:
            return False
        tok.owner_id = to_owner_id
        self._listings.pop(token_id, None)
        return True

    def list_item_token(
        self, owner_id: str, token_id: str, price_simcoin: float
    ) -> bool:
        tok = self._items.get(token_id)
        if not tok or tok.owner_id != owner_id:
            return False
        if price_simcoin <= 0:
            return False
        self._listings[token_id] = {
            "token_id": token_id,
            "owner_id": owner_id,
            "price_simcoin": float(price_simcoin),
        }
        return True

    def cancel_listing(self, owner_id: str, token_id: str) -> bool:
        listing = self._listings.get(token_id)
        if not listing or listing.get("owner_id") != owner_id:
            return False
        self._listings.pop(token_id, None)
        return True

    def buy_listed_token(self, buyer_id: str, token_id: str) -> bool:
        listing = self._listings.get(token_id)
        tok = self._items.get(token_id)
        if not listing or not tok:
            return False
        seller_id = str(listing.get("owner_id", ""))
        price = float(listing.get("price_simcoin", 0.0))
        if not self.transfer_simcoin(buyer_id, seller_id, price):
            return False
        tok.owner_id = buyer_id
        self._listings.pop(token_id, None)
        return True

    def marketplace(self) -> list[dict]:
        out = []
        for token_id, listing in self._listings.items():
            tok = self._items.get(token_id)
            if not tok:
                continue
            out.append(
                {
                    "token_id": token_id,
                    "owner_id": listing.get("owner_id"),
                    "price_simcoin": round(float(listing.get("price_simcoin", 0.0)), 4),
                    "item_ref": tok.item_ref,
                    "rarity": tok.rarity,
                }
            )
        return out

    def wallet(self, sim_id: str) -> dict:
        self.ensure_wallet(sim_id)
        items = [
            {
                "token_id": t.token_id,
                "item_ref": t.item_ref,
                "rarity": t.rarity,
                "minted_tick": t.minted_tick,
            }
            for t in self._items.values()
            if t.owner_id == sim_id
        ]
        return {
            "simcoin": round(self._simcoin.get(sim_id, 0.0), 4),
            "item_tokens": items,
        }

    def state(self) -> dict:
        return {
            "wallets": len(self._simcoin),
            "item_tokens": len(self._items),
            "simcoin_supply": round(sum(self._simcoin.values()), 4),
            "listings": len(self._listings),
        }

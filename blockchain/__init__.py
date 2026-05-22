"""
blockchain/ — SimChain: a purpose-built PoA blockchain for the sim economy.

Consensus:   Proof of Authority (server node is sole validator)
Signing:     secp256k1 via eth_account (Ethereum-compatible 0x addresses)
Block time:  every CHAIN_BLOCK_INTERVAL sim ticks (default 5)
Finality:    immediate (no forks in PoA with a single validator)
Transport:   NATS (simchain.blocks subject) when networked; in-process otherwise

Contracts (Python classes, not EVM bytecode):
  simcoin       — ERC-20-like $SIM token (1 SIM = 1 simoleon)
  shop_registry — tamper-evident shop / gig / property transaction log
  sim_agreement — loans, employment, partnerships with on-chain enforcement
  stock_market  — 8 sector stocks priced by sim activity

Bridge:  world/web3_bridge.py wraps the chain for the engine tick loop.
"""

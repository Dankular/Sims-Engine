"""
patch_ledger.py — Redirect all direct simoleon mutations through engine._tx().

Run once:  python patch_ledger.py
Safe to re-run: idempotent (skips if already patched).
"""
import sys

def tx_income(var, tx_const, indent, description, extra="", counterpart=""):
    cp = f", counterpart={counterpart}" if counterpart else ""
    meta = f", metadata={extra}" if extra else ""
    return (
        f"_eng = getattr(sim, '_engine_ref', None)\n"
        f"{indent}if _eng:\n"
        f"{indent}    from persistence.ledger import {tx_const}\n"
        f"{indent}    _eng._tx(sim, {var}, {tx_const}{cp}, description={description!r}{meta})\n"
        f"{indent}else:\n"
        f"{indent}    sim.simoleons += {var}"
    )

def tx_expense(var, tx_const, indent, description, counterpart=""):
    cp = f", counterpart={counterpart}" if counterpart else ""
    return (
        f"_eng = getattr(sim, '_engine_ref', None)\n"
        f"{indent}if _eng:\n"
        f"{indent}    from persistence.ledger import {tx_const}\n"
        f"{indent}    _eng._tx(sim, -abs({var}), {tx_const}{cp}, description={description!r})\n"
        f"{indent}else:\n"
        f"{indent}    sim.simoleons = max(0.0, sim.simoleons - {var})"
    )

PATCHES = {
    "world/gigs.py": [
        (
            "sim.simoleons += gig.pay",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "        if _eng:\n"
            "            from persistence.ledger import TX_GIG_PAYOUT\n"
            "            _eng._tx(sim, gig.pay, TX_GIG_PAYOUT, counterpart=str(getattr(gig,'gig_type','')),\n"
            "                     description='gig payout')\n"
            "        else:\n"
            "            sim.simoleons += gig.pay"
        ),
    ],
    "world/crafting.py": [
        (
            "sim.simoleons += item.royalty_per_tick",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "                if _eng:\n"
            "                    from persistence.ledger import TX_CRAFTING_ROYALTY\n"
            "                    _eng._tx(sim, item.royalty_per_tick, TX_CRAFTING_ROYALTY,\n"
            "                             description='crafting royalty')\n"
            "                else:\n"
            "                    sim.simoleons += item.royalty_per_tick"
        ),
        (
            "sim.simoleons += item.sell_value",
            "_eng = engine\n"
            "                if _eng and hasattr(_eng, '_tx'):\n"
            "                    from persistence.ledger import TX_CRAFTING_SALE\n"
            "                    _eng._tx(sim, item.sell_value, TX_CRAFTING_SALE, description='crafted item sale')\n"
            "                else:\n"
            "                    sim.simoleons += item.sell_value"
        ),
    ],
    "world/bookie.py": [
        (
            "sim.simoleons += bet.payout",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "                    if _eng:\n"
            "                        from persistence.ledger import TX_BETTING_WIN\n"
            "                        _eng._tx(sim, bet.payout, TX_BETTING_WIN, description='bet win')\n"
            "                    else:\n"
            "                        sim.simoleons += bet.payout"
        ),
    ],
    "world/burglar.py": [
        (
            "sim.simoleons += split",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "            if _eng:\n"
            "                from persistence.ledger import TX_BURGLAR_TAKE\n"
            "                _eng._tx(sim, split, TX_BURGLAR_TAKE, description='burglary proceeds')\n"
            "            else:\n"
            "                sim.simoleons += split"
        ),
    ],
    "world/skill_classes.py": [
        (
            "host.simoleons += payout",
            "_eng = getattr(host, '_engine_ref', None)\n"
            "         if _eng:\n"
            "             from persistence.ledger import TX_SKILL_CLASS_HOST\n"
            "             _eng._tx(host, payout, TX_SKILL_CLASS_HOST, description='skill class host fee')\n"
            "         else:\n"
            "             host.simoleons += payout"
        ),
    ],
    "world/neighborhoods.py": [
        (
            "sim.simoleons += gain * 0.2",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "                if _eng:\n"
            "                    from persistence.ledger import TX_NEIGHBORHOOD_BIZ\n"
            "                    _eng._tx(sim, gain * 0.2, TX_NEIGHBORHOOD_BIZ, description='neighborhood biz income')\n"
            "                else:\n"
            "                    sim.simoleons += gain * 0.2"
        ),
        (
            "sim.simoleons = max(0.0, sim.simoleons - travel_cost)",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "                if _eng:\n"
            "                    from persistence.ledger import TX_TRAVEL_COST\n"
            "                    _eng._tx(sim, -travel_cost, TX_TRAVEL_COST, description='travel cost')\n"
            "                else:\n"
            "                    sim.simoleons = max(0.0, sim.simoleons - travel_cost)"
        ),
    ],
    "world/objects.py": [
        (
            "sim.simoleons += payout",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "        if _eng:\n"
            "            from persistence.ledger import TX_OBJECT_SALE\n"
            "            _eng._tx(sim, payout, TX_OBJECT_SALE, description='item sold')\n"
            "        else:\n"
            "            sim.simoleons += payout"
        ),
    ],
    "world/property.py": [
        (
            "owner.simoleons += payout / 14.0",
            "_eng = getattr(owner, '_engine_ref', None)\n"
            "            if _eng:\n"
            "                from persistence.ledger import TX_PROPERTY_DIVIDEND\n"
            "                _eng._tx(owner, payout / 14.0, TX_PROPERTY_DIVIDEND,\n"
            "                         counterpart=prop.property_id, description=f'property dividend: {prop.name}')\n"
            "            else:\n"
            "                owner.simoleons += payout / 14.0"
        ),
        (
            "owner.simoleons = max(0.0, owner.simoleons - maint - tax)",
            "_eng = getattr(owner, '_engine_ref', None)\n"
            "            if _eng:\n"
            "                from persistence.ledger import TX_PROPERTY_MAINTENANCE, TX_PROPERTY_TAX\n"
            "                if maint > 0:\n"
            "                    _eng._tx(owner, -maint, TX_PROPERTY_MAINTENANCE,\n"
            "                             counterpart=prop.property_id, description=f'maintenance: {prop.name}')\n"
            "                if tax > 0:\n"
            "                    _eng._tx(owner, -tax, TX_PROPERTY_TAX,\n"
            "                             counterpart=prop.property_id, description=f'property tax: {prop.name}')\n"
            "            else:\n"
            "                owner.simoleons = max(0.0, owner.simoleons - maint - tax)"
        ),
        (
            "sim.simoleons += 120.0",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "                if _eng:\n"
            "                    from persistence.ledger import TX_BURGLAR_TAKE\n"
            "                    _eng._tx(sim, 120.0, TX_BURGLAR_TAKE, description='criminal property income')\n"
            "                else:\n"
            "                    sim.simoleons += 120.0"
        ),
    ],
    "world/career_manager.py": [
        (
            "sim.simoleons += round(level_def.salary_per_tick * bonus, 2)",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "        if _eng:\n"
            "            from persistence.ledger import TX_SALARY\n"
            "            _eng._tx(sim, round(level_def.salary_per_tick * bonus, 2), TX_SALARY,\n"
            "                     counterpart=getattr(sim, 'career_id', ''),\n"
            "                     description=f'career salary: {self._get_title(sim)}')\n"
            "        else:\n"
            "            sim.simoleons += round(level_def.salary_per_tick * bonus, 2)"
        ),
        (
            "sim.simoleons += round(level_def.salary_per_tick * bonus, 2)",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "        if _eng:\n"
            "            from persistence.ledger import TX_SALARY\n"
            "            _eng._tx(sim, round(level_def.salary_per_tick * bonus, 2), TX_SALARY,\n"
            "                     counterpart=getattr(sim, 'career_id', ''),\n"
            "                     description=f'career salary: {self._get_title(sim)}')\n"
            "        else:\n"
            "            sim.simoleons += round(level_def.salary_per_tick * bonus, 2)"
        ),
        (
            "sim.simoleons += round(level_def.salary_per_tick * bonus, 2)",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "        if _eng:\n"
            "            from persistence.ledger import TX_SALARY\n"
            "            _eng._tx(sim, round(level_def.salary_per_tick * bonus, 2), TX_SALARY,\n"
            "                     counterpart=getattr(sim, 'career_id', ''),\n"
            "                     description=f'career salary: {self._get_title(sim)}')\n"
            "        else:\n"
            "            sim.simoleons += round(level_def.salary_per_tick * bonus, 2)"
        ),
        (
            "sim.simoleons += round(level_def.salary_per_tick * bonus, 2)",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "        if _eng:\n"
            "            from persistence.ledger import TX_SALARY\n"
            "            _eng._tx(sim, round(level_def.salary_per_tick * bonus, 2), TX_SALARY,\n"
            "                     counterpart=getattr(sim, 'career_id', ''),\n"
            "                     description=f'career salary: {self._get_title(sim)}')\n"
            "        else:\n"
            "            sim.simoleons += round(level_def.salary_per_tick * bonus, 2)"
        ),
    ],
    "world/contracts.py": [
        (
            "borrower.simoleons += principal",
            "_eng = getattr(borrower, '_engine_ref', None)\n"
            "                lender.simoleons -= principal\n"
            "                if _eng:\n"
            "                    from persistence.ledger import TX_CONTRACT_LOAN_RECV\n"
            "                    _eng._tx(borrower, principal, TX_CONTRACT_LOAN_RECV,\n"
            "                             counterpart=lender.sim_id, description=f'loan disbursed: {c.contract_id[:8]}')\n"
            "                else:\n"
            "                    borrower.simoleons += principal"
        ),
        (
            "lender.simoleons += due",
            "_eng = getattr(lender, '_engine_ref', None)\n"
            "                if _eng:\n"
            "                    from persistence.ledger import TX_CONTRACT_SETTLED_RECV\n"
            "                    _eng._tx(lender, due, TX_CONTRACT_SETTLED_RECV,\n"
            "                             counterpart=borrower.sim_id, description=f'loan repaid: {c.contract_id[:8]}')\n"
            "                else:\n"
            "                    lender.simoleons += due"
        ),
        (
            "employee.simoleons += wage",
            "_eng = getattr(employee, '_engine_ref', None)\n"
            "            if _eng:\n"
            "                from persistence.ledger import TX_CONTRACT_SALARY_RECV\n"
            "                _eng._tx(employee, wage, TX_CONTRACT_SALARY_RECV,\n"
            "                         counterpart=employer.sim_id, description='employment salary')\n"
            "            else:\n"
            "                employee.simoleons += wage"
        ),
        (
            "employee.simoleons += pay",
            "_eng = getattr(employee, '_engine_ref', None)\n"
            "            if _eng:\n"
            "                from persistence.ledger import TX_CONTRACT_SALARY_RECV\n"
            "                _eng._tx(employee, pay, TX_CONTRACT_SALARY_RECV,\n"
            "                         counterpart=str(getattr(employer, 'sim_id', '')), description='employment pay')\n"
            "            else:\n"
            "                employee.simoleons += pay"
        ),
    ],
    "world/institutions.py": [
        (
            "sim.simoleons += payout",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "                if _eng:\n"
            "                    from persistence.ledger import TX_UNION_SUPPORT\n"
            "                    _eng._tx(sim, payout, TX_UNION_SUPPORT, description='union hardship payment')\n"
            "                else:\n"
            "                    sim.simoleons += payout"
        ),
    ],
    "world/action_packs.py": [
        (
            "sim.simoleons += 10.0",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "        if _eng:\n"
            "            from persistence.ledger import TX_ACTION_PACK_INCOME\n"
            "            _eng._tx(sim, 10.0, TX_ACTION_PACK_INCOME, description='action pack reward')\n"
            "        else:\n"
            "            sim.simoleons += 10.0"
        ),
        (
            "sim.simoleons = max(0.0, sim.simoleons - 20.0)",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "            if _eng:\n"
            "                from persistence.ledger import TX_ACTION_PACK_COST\n"
            "                _eng._tx(sim, -20.0, TX_ACTION_PACK_COST, description='action pack cost')\n"
            "            else:\n"
            "                sim.simoleons = max(0.0, sim.simoleons - 20.0)"
        ),
        (
            "sim.simoleons = max(0.0, sim.simoleons - 12.0)",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "        if _eng:\n"
            "            from persistence.ledger import TX_ACTION_PACK_COST\n"
            "            _eng._tx(sim, -12.0, TX_ACTION_PACK_COST, description='action pack cost')\n"
            "        else:\n"
            "            sim.simoleons = max(0.0, sim.simoleons - 12.0)"
        ),
    ],
    "core/lifetime_aspirations.py": [
        (
            "sim.simoleons += 1000.0",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "            if _eng:\n"
            "                from persistence.ledger import TX_LIFETIME_REWARD\n"
            "                _eng._tx(sim, 1000.0, TX_LIFETIME_REWARD, description='aspiration reward')\n"
            "            else:\n"
            "                sim.simoleons += 1000.0"
        ),
    ],
    "core/lifetime_wish.py": [
        (
            "sim.simoleons += bonus_cash",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "    if _eng:\n"
            "        from persistence.ledger import TX_LIFETIME_REWARD\n"
            "        _eng._tx(sim, bonus_cash, TX_LIFETIME_REWARD, description='lifetime wish cash bonus')\n"
            "    else:\n"
            "        sim.simoleons += bonus_cash"
        ),
    ],
    "narrative/event_templates.py": [
        (
            "sim.simoleons += amount",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "    if _eng:\n"
            "        from persistence.ledger import TX_INHERITANCE\n"
            "        _eng._tx(sim, amount, TX_INHERITANCE, description='inheritance received')\n"
            "    else:\n"
            "        sim.simoleons += amount"
        ),
    ],
    "core/consequences_hard.py": [
        (
            "sim.simoleons = 0.0",
            "_eng = getattr(sim, '_engine_ref', None)\n"
            "            if _eng and sim.simoleons != 0.0:\n"
            "                from persistence.ledger import TX_BANKRUPTCY_SEIZURE\n"
            "                _eng._tx(sim, -sim.simoleons, TX_BANKRUPTCY_SEIZURE,\n"
            "                         description='bankruptcy asset seizure', allow_overdraft=True)\n"
            "            else:\n"
            "                sim.simoleons = 0.0"
        ),
    ],
}

patched = 0
missed  = 0
for filepath, changes in PATCHES.items():
    with open(filepath, 'r', encoding='utf-8') as f:
        src = f.read()
    original = src
    for old, new in changes:
        if old in src:
            src = src.replace(old, new, 1)
            patched += 1
        else:
            print(f"  MISS in {filepath}: {old[:50]!r}")
            missed += 1
    if src != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(src)
        print(f"  OK: {filepath}")

print(f"\nPatched: {patched}  Missed: {missed}")

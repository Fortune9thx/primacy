# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }
#
# PRIMACY -- a permissionless hourly primacy market on GenLayer Studio Dev
# (chain 61997). See README.md for the full steward brief and docs/ for
# the state machine and threat model.
#
# NOTE ON THE DEPENDS HASH: the build brief for this project quoted the
# older, now-dead `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6`
# hash. That hash's runner asset has been removed from GenLayer's own
# release hosting (confirmed across multiple prior projects on this
# account -- upstream infra, not a local config problem). The hash pinned
# above is the one confirmed live-working against the pinned RC toolchain
# in this repo (genlayer-test==0.30.0rc2 / genvm-linter==0.11.1rc2 /
# genlayer-py==0.19.0rc2) via `genvm-lint check`, and separately confirmed
# live on Studio Devnet by prior projects on this account. See
# docs/architecture.md for the full toolchain-version table.
import json
from datetime import datetime, timezone

import genlayer as gl
from genlayer.storage import DynArray, TreeMap
from genlayer.types import Address, u256

from primacy_lib import (
    CREATE_BOND,
    HOUR_SECONDS,
    LANES,
    MAX_OPEN_MARKETS_PER_CREATOR,
    MAX_PAGE_SIZE,
    MIN_BET,
    MIN_LEAD_SECONDS,
    SETTLE_BOND,
    SETTLE_WINDOW_SECONDS,
    VENUES,
    build_evidence,
    build_venue_url,
    compare_evidence,
    compute_claim_payout,
    compute_fee,
    get_constitution_dict,
    is_hour_boundary,
    parse_response_body,
)


@gl.evm.contract_interface
class _Recipient:
    class View:
        pass

    class Write:
        pass


class Primacy(gl.contract.Contract):
    treasury: Address
    next_market_id: u256

    market_creator: TreeMap[str, str]
    market_lane: TreeMap[str, str]
    market_start: TreeMap[str, u256]
    market_state: TreeMap[str, str]
    market_winner: TreeMap[str, str]
    market_settler: TreeMap[str, str]
    market_total_pool: TreeMap[str, u256]
    market_create_bond_returned: TreeMap[str, u256]
    market_settle_bond_returned: TreeMap[str, u256]
    market_create_bond_slashed: TreeMap[str, u256]
    market_claimed_stake: TreeMap[str, u256]
    market_claimed_amount: TreeMap[str, u256]
    market_evidence_json: TreeMap[str, str]

    lane_start_index: TreeMap[str, str]
    creator_open_count: TreeMap[str, u256]

    position_amount: TreeMap[str, u256]
    position_symbol: TreeMap[str, str]
    position_claimed: TreeMap[str, u256]
    pool_by_symbol: TreeMap[str, u256]

    user_market_ids: TreeMap[str, DynArray[str]]
    all_market_ids: DynArray[str]

    def __init__(self, treasury: str):
        self.treasury = Address(treasury)
        self.next_market_id = u256(1)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _now_unix(self) -> int:
        # datetime.now(timezone.utc) is pinned to the transaction's own
        # deterministic datetime on GenVM (confirmed live, all validators
        # see the identical value) -- see docs/architecture.md. Preferred
        # over gl.message.raw["datetime"] here because it is also the API
        # gltest direct-mode's vm.warp() actually makes testable.
        return int(datetime.now(timezone.utc).timestamp())

    def _page_bounds(self, total: int, cursor: u256, limit: u256) -> tuple:
        lim = int(limit)
        if lim <= 0 or lim > MAX_PAGE_SIZE:
            lim = MAX_PAGE_SIZE
        start = int(cursor)
        if start < 0:
            start = 0
        end = start + lim
        if end > total:
            end = total
        return start, end

    def _market_view(self, mid: str) -> dict:
        lane_id = self.market_lane[mid]
        symbols = LANES[lane_id]
        pools = {sym: int(self.pool_by_symbol.get(f"{mid}:{sym}", u256(0))) for sym in symbols}
        start = int(self.market_start[mid])
        return {
            "market_id": int(mid),
            "creator": self.market_creator[mid],
            "lane_id": lane_id,
            "symbols": list(symbols),
            "start": start,
            "end": start + HOUR_SECONDS,
            "state": self.market_state[mid],
            "winner": self.market_winner[mid] or None,
            "settler": self.market_settler[mid] or None,
            "total_pool": int(self.market_total_pool[mid]),
            "pool_by_symbol": pools,
            "create_bond_returned": bool(int(self.market_create_bond_returned[mid])),
            "create_bond_slashed": bool(int(self.market_create_bond_slashed[mid])),
            "settle_bond_returned": bool(int(self.market_settle_bond_returned[mid])),
        }

    def _finalize(self, mid: str, status: str, winner, settler: str, evidence: dict | None) -> None:
        self.market_state[mid] = status
        self.market_winner[mid] = winner or ""
        self.market_settler[mid] = settler
        if evidence is not None:
            self.market_evidence_json[mid] = json.dumps(evidence, sort_keys=True)

        creator = self.market_creator[mid]
        cur = int(self.creator_open_count.get(creator, u256(0)))
        if cur > 0:
            self.creator_open_count[creator] = u256(cur - 1)

        total_pool = int(self.market_total_pool[mid])
        if status == "SETTLED" and winner is not None and total_pool > 0:
            fee = compute_fee(total_pool)
            settler_fee = fee // 2
            treasury_fee = fee - settler_fee
            if settler_fee > 0:
                _Recipient(Address(settler)).emit_transfer(value=u256(settler_fee))
            if treasury_fee > 0:
                _Recipient(self.treasury).emit_transfer(value=u256(treasury_fee))

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    @gl.public.write.payable
    def create_market(self, lane_id: str, start: u256) -> u256:
        if lane_id not in LANES:
            raise gl.vm.UserError("unknown lane")
        start_i = int(start)
        if not is_hour_boundary(start_i):
            raise gl.vm.UserError("not hour boundary")
        now = self._now_unix()
        if start_i < now + MIN_LEAD_SECONDS:
            raise gl.vm.UserError("below min lead")
        if int(gl.message.value) != CREATE_BOND:
            raise gl.vm.UserError("wrong bond amount")

        lane_start_key = f"{lane_id}:{start_i}"
        if lane_start_key in self.lane_start_index:
            raise gl.vm.UserError("duplicate market")

        creator = gl.message.sender_address.as_hex
        open_count = int(self.creator_open_count.get(creator, u256(0)))
        if open_count >= MAX_OPEN_MARKETS_PER_CREATOR:
            raise gl.vm.UserError("creator cap reached")

        market_id = self.next_market_id
        self.next_market_id = u256(int(market_id) + 1)
        mid = str(market_id)

        self.market_creator[mid] = creator
        self.market_lane[mid] = lane_id
        self.market_start[mid] = start
        self.market_state[mid] = "OPEN"
        self.market_winner[mid] = ""
        self.market_settler[mid] = ""
        self.market_total_pool[mid] = u256(0)
        self.market_create_bond_returned[mid] = u256(0)
        self.market_settle_bond_returned[mid] = u256(0)
        self.market_create_bond_slashed[mid] = u256(0)
        self.market_claimed_stake[mid] = u256(0)
        self.market_claimed_amount[mid] = u256(0)
        self.lane_start_index[lane_start_key] = mid
        self.creator_open_count[creator] = u256(open_count + 1)
        self.all_market_ids.append(mid)

        return market_id

    @gl.public.write.payable
    def place_bet(self, market_id: u256, symbol: str) -> str:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        lane_id = self.market_lane[mid]
        lane_symbols = LANES[lane_id]
        if symbol not in lane_symbols:
            raise gl.vm.UserError("unknown symbol")
        if self.market_state[mid] != "OPEN":
            raise gl.vm.UserError("betting closed")
        start = int(self.market_start[mid])
        now = self._now_unix()
        if now >= start:
            raise gl.vm.UserError("betting closed")
        if int(gl.message.value) < MIN_BET:
            raise gl.vm.UserError("below min bet")

        bettor = gl.message.sender_address.as_hex
        pos_key = f"{mid}:{bettor}"
        existing_symbol = self.position_symbol.get(pos_key, "")
        if existing_symbol != "" and existing_symbol != symbol:
            raise gl.vm.UserError("side locked")

        amount = int(gl.message.value)
        self.position_amount[pos_key] = u256(int(self.position_amount.get(pos_key, u256(0))) + amount)
        self.position_symbol[pos_key] = symbol

        pool_key = f"{mid}:{symbol}"
        self.pool_by_symbol[pool_key] = u256(int(self.pool_by_symbol.get(pool_key, u256(0))) + amount)
        self.market_total_pool[mid] = u256(int(self.market_total_pool[mid]) + amount)

        if existing_symbol == "":
            self.user_market_ids.get_or_insert_default(bettor).append(mid)

        return "ok"

    @gl.public.write.payable
    def settle_market(self, market_id: u256) -> str:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        if int(gl.message.value) != SETTLE_BOND:
            raise gl.vm.UserError("wrong bond amount")

        state = self.market_state[mid]
        if state != "OPEN":
            raise gl.vm.UserError("already settled")

        start = int(self.market_start[mid])
        end = start + HOUR_SECONDS
        lane_id = self.market_lane[mid]
        lane_symbols = LANES[lane_id]
        now = self._now_unix()
        settler = gl.message.sender_address.as_hex

        if now < end:
            raise gl.vm.UserError("not expired")

        if now >= end + SETTLE_WINDOW_SECONDS:
            self._finalize(
                mid,
                "INCONCLUSIVE",
                None,
                settler,
                {"status": "INCONCLUSIVE", "reason": "settle_window_expired", "start": start, "lane_id": lane_id},
            )
            return "INCONCLUSIVE"

        def _fetch_all() -> dict:
            per_venue: dict = {}
            for venue in VENUES:
                candles: dict = {}
                for sym in lane_symbols:
                    url = build_venue_url(venue, sym, start)
                    try:
                        resp = gl.nondet.web.get(url)
                    except Exception:
                        candles[sym] = {"ok": False, "reason": "fetch_error"}
                        continue
                    status = getattr(resp, "status", 200)
                    if status != 200:
                        candles[sym] = {"ok": False, "reason": "non_200"}
                        continue
                    body = getattr(resp, "body", resp)
                    if isinstance(body, (bytes, bytearray)):
                        raw = body.decode("utf-8", errors="ignore")
                    else:
                        raw = body
                    candles[sym] = parse_response_body(venue, raw, start)
                per_venue[venue] = candles
            return build_evidence(mid, lane_id, start, per_venue)

        def _validator(leader_res) -> bool:
            try:
                leader_val = leader_res.calldata
            except AttributeError:
                return False
            if not isinstance(leader_val, dict):
                return False
            mine = _fetch_all()
            return compare_evidence(mine, leader_val, lane_symbols)

        evidence = gl.vm.run_nondet(_fetch_all, _validator)

        status = evidence["status"]
        winner = evidence["winner"]

        if status == "SETTLED" and winner is not None:
            winning_pool = int(self.pool_by_symbol.get(f"{mid}:{winner}", u256(0)))
            if winning_pool == 0:
                evidence = dict(evidence)
                evidence["audit_winner"] = winner
                evidence["winner"] = None
                evidence["status"] = "INCONCLUSIVE"
                self._finalize(mid, "INCONCLUSIVE", None, settler, evidence)
                return "INCONCLUSIVE"
            self._finalize(mid, "SETTLED", winner, settler, evidence)
            return "SETTLED"

        self._finalize(mid, "INCONCLUSIVE", None, settler, evidence)
        return "INCONCLUSIVE"

    @gl.public.write
    def claim(self, market_id: u256) -> u256:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        if self.market_state[mid] != "SETTLED":
            raise gl.vm.UserError("nothing to claim")

        claimant = gl.message.sender_address.as_hex
        pos_key = f"{mid}:{claimant}"
        winner = self.market_winner[mid]
        my_symbol = self.position_symbol.get(pos_key, "")
        my_stake = int(self.position_amount.get(pos_key, u256(0)))
        already = int(self.position_claimed.get(pos_key, u256(0)))
        if my_symbol != winner or my_stake == 0 or already == 1:
            raise gl.vm.UserError("nothing to claim")

        total_pool = int(self.market_total_pool[mid])
        fee = compute_fee(total_pool)
        distributable = total_pool - fee
        winning_pool_total = int(self.pool_by_symbol[f"{mid}:{winner}"])
        claimed_stake_before = int(self.market_claimed_stake[mid])
        claimed_amount_before = int(self.market_claimed_amount[mid])

        payout = compute_claim_payout(
            my_stake, winning_pool_total, distributable, claimed_stake_before, claimed_amount_before
        )

        self.position_claimed[pos_key] = u256(1)
        self.market_claimed_stake[mid] = u256(claimed_stake_before + my_stake)
        self.market_claimed_amount[mid] = u256(claimed_amount_before + payout)

        if payout > 0:
            _Recipient(gl.message.sender_address).emit_transfer(value=u256(payout))

        return u256(payout)

    @gl.public.write
    def claim_refund(self, market_id: u256) -> u256:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        if self.market_state[mid] != "INCONCLUSIVE":
            raise gl.vm.UserError("not inconclusive")

        claimant = gl.message.sender_address.as_hex
        pos_key = f"{mid}:{claimant}"
        my_stake = int(self.position_amount.get(pos_key, u256(0)))
        already = int(self.position_claimed.get(pos_key, u256(0)))
        if my_stake == 0 or already == 1:
            raise gl.vm.UserError("nothing to claim")

        self.position_claimed[pos_key] = u256(1)
        _Recipient(gl.message.sender_address).emit_transfer(value=u256(my_stake))
        return u256(my_stake)

    @gl.public.write
    def reclaim_bonds(self, market_id: u256) -> u256:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        if self.market_state[mid] == "OPEN":
            raise gl.vm.UserError("not expired")

        caller = gl.message.sender_address.as_hex
        creator = self.market_creator[mid]
        settler = self.market_settler[mid]
        total_pool = int(self.market_total_pool[mid])

        did_something = False
        owed_to_caller = 0

        if int(self.market_create_bond_returned[mid]) == 0:
            if total_pool == 0:
                self.market_create_bond_returned[mid] = u256(1)
                self.market_create_bond_slashed[mid] = u256(1)
                _Recipient(self.treasury).emit_transfer(value=u256(CREATE_BOND))
                did_something = True
            elif caller == creator:
                self.market_create_bond_returned[mid] = u256(1)
                owed_to_caller += CREATE_BOND
                did_something = True

        if settler != "" and caller == settler and int(self.market_settle_bond_returned[mid]) == 0:
            self.market_settle_bond_returned[mid] = u256(1)
            owed_to_caller += SETTLE_BOND
            did_something = True

        if not did_something:
            raise gl.vm.UserError("nothing to claim")

        if owed_to_caller > 0:
            _Recipient(gl.message.sender_address).emit_transfer(value=u256(owed_to_caller))

        return u256(owed_to_caller)

    # ------------------------------------------------------------------
    # views
    # ------------------------------------------------------------------

    @gl.public.view
    def get_constitution(self) -> dict:
        d = get_constitution_dict()
        d["treasury"] = self.treasury.as_hex
        return d

    @gl.public.view
    def get_config(self) -> dict:
        return {
            "treasury": self.treasury.as_hex,
            "next_market_id": int(self.next_market_id),
            "total_markets": len(self.all_market_ids),
        }

    @gl.public.view
    def get_lanes(self) -> dict:
        return {k: list(v) for k, v in LANES.items()}

    @gl.public.view
    def get_lane(self, lane_id: str) -> list:
        if lane_id not in LANES:
            raise gl.vm.UserError("unknown lane")
        return list(LANES[lane_id])

    @gl.public.view
    def get_market(self, market_id: u256) -> dict:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        return self._market_view(mid)

    @gl.public.view
    def get_markets(self, cursor: u256, limit: u256, state_filter: str) -> list:
        ids = [self.all_market_ids[i] for i in range(len(self.all_market_ids))]
        if state_filter:
            ids = [mid for mid in ids if self.market_state[mid] == state_filter]
        start, end = self._page_bounds(len(ids), cursor, limit)
        return [self._market_view(mid) for mid in ids[start:end]]

    @gl.public.view
    def get_open_markets(self, cursor: u256, limit: u256) -> list:
        return self.get_markets(cursor, limit, "OPEN")

    @gl.public.view
    def get_board(self) -> list:
        ids = [self.all_market_ids[i] for i in range(len(self.all_market_ids))]
        open_ids = [mid for mid in ids if self.market_state[mid] == "OPEN"]
        return [self._market_view(mid) for mid in open_ids[:MAX_PAGE_SIZE]]

    @gl.public.view
    def get_betting_state(self, market_id: u256, address: str) -> dict:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        addr = Address(address).as_hex
        pos_key = f"{mid}:{addr}"
        start = int(self.market_start[mid])
        now = self._now_unix()
        is_open = self.market_state[mid] == "OPEN" and now < start
        return {
            "state": self.market_state[mid],
            "is_open": is_open,
            "seconds_until_close": max(0, start - now),
            "my_symbol": self.position_symbol.get(pos_key, "") or None,
            "my_stake": int(self.position_amount.get(pos_key, u256(0))),
        }

    @gl.public.view
    def get_user_position(self, market_id: u256, address: str) -> dict:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        addr = Address(address).as_hex
        pos_key = f"{mid}:{addr}"
        return {
            "market_id": int(market_id),
            "symbol": self.position_symbol.get(pos_key, "") or None,
            "amount": int(self.position_amount.get(pos_key, u256(0))),
            "claimed": bool(int(self.position_claimed.get(pos_key, u256(0)))),
        }

    @gl.public.view
    def get_user_positions(self, address: str, cursor: u256, limit: u256) -> list:
        addr = Address(address).as_hex
        ids = self.user_market_ids.get(addr, None)
        mids = [ids[i] for i in range(len(ids))] if ids is not None else []
        start, end = self._page_bounds(len(mids), cursor, limit)
        out = []
        for mid in mids[start:end]:
            pos_key = f"{mid}:{addr}"
            out.append({
                "market_id": int(mid),
                "state": self.market_state[mid],
                "symbol": self.position_symbol.get(pos_key, "") or None,
                "amount": int(self.position_amount.get(pos_key, u256(0))),
                "claimed": bool(int(self.position_claimed.get(pos_key, u256(0)))),
            })
        return out

    @gl.public.view
    def get_claimable_markets(self, address: str, cursor: u256, limit: u256) -> list:
        addr = Address(address).as_hex
        ids = self.user_market_ids.get(addr, None)
        mids = [ids[i] for i in range(len(ids))] if ids is not None else []
        claimable = []
        for mid in mids:
            state = self.market_state[mid]
            if state == "OPEN":
                continue
            pos_key = f"{mid}:{addr}"
            already = bool(int(self.position_claimed.get(pos_key, u256(0))))
            if already:
                continue
            my_stake = int(self.position_amount.get(pos_key, u256(0)))
            if my_stake == 0:
                continue
            if state == "SETTLED" and self.position_symbol.get(pos_key, "") != self.market_winner[mid]:
                continue
            claimable.append(mid)
        start, end = self._page_bounds(len(claimable), cursor, limit)
        return [self._market_view(mid) for mid in claimable[start:end]]

    @gl.public.view
    def get_source_evidence(self, market_id: u256) -> dict:
        mid = str(market_id)
        if mid not in self.market_state:
            raise gl.vm.UserError("market not found")
        raw = self.market_evidence_json.get(mid, "")
        if not raw:
            return {}
        return json.loads(raw)

    @gl.public.view
    def get_user_activity(self, address: str, cursor: u256, limit: u256) -> list:
        return self.get_user_positions(address, cursor, limit)

    @gl.public.view
    def get_keeper_stats(self) -> dict:
        total = len(self.all_market_ids)
        now = self._now_unix()
        settleable = 0
        for i in range(total):
            mid = self.all_market_ids[i]
            if self.market_state[mid] != "OPEN":
                continue
            end = int(self.market_start[mid]) + HOUR_SECONDS
            if now >= end:
                settleable += 1
        return {"total_markets": total, "settleable_markets": settleable}

    @gl.public.view
    def get_market_by_lane_start(self, lane_id: str, start: u256) -> int | None:
        key = f"{lane_id}:{int(start)}"
        mid = self.lane_start_index.get(key, "")
        if not mid:
            return None
        return int(mid)

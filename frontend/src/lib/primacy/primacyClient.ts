/**
 * PRIMACY typed client -- read helpers (memoized singleton, no account)
 * and write helpers (bind the wallet's real provider, never
 * window.ethereum directly). See README.md section 8 and
 * docs/architecture.md.
 */
import { createClient } from "genlayer-js";
import { PRIMACY_CHAIN, PRIMACY_CONTRACT_ADDRESS } from "./networks";

export type Lane = "CRYPTO_EQUITY_PROXIES" | "MAJORS";

export interface Constitution {
  lanes: Record<Lane, string[]>;
  venues: string[];
  hour_seconds: number;
  min_lead_seconds: number;
  min_bet_wei: string;
  create_bond_wei: string;
  settle_bond_wei: string;
  fee_bps: number;
  settle_window_seconds: number;
  bps_tol: number;
  max_open_markets_per_creator: number;
  max_page_size: number;
  instrument_label: string;
  treasury: string;
}

export interface MarketView {
  market_id: number;
  creator: string;
  lane_id: Lane;
  symbols: string[];
  start: number;
  end: number;
  state: "OPEN" | "SETTLED" | "INCONCLUSIVE";
  winner: string | null;
  settler: string | null;
  total_pool: string;
  pool_by_symbol: Record<string, string>;
  create_bond_returned: boolean;
  create_bond_slashed: boolean;
  settle_bond_returned: boolean;
}

/**
 * Read-only client, memoized once, module-level singleton -- confirmed
 * correct pattern (a prior GenLayer Portal rejection on this account was
 * for a read client generating a fresh ephemeral account per call,
 * triggering repeated wallet permission prompts). Takes NO account/provider.
 */
let _readClient: ReturnType<typeof createClient> | null = null;

export function getReadClient() {
  if (!_readClient) {
    _readClient = createClient({ chain: PRIMACY_CHAIN });
  }
  return _readClient;
}

async function readView<T>(functionName: string, args: unknown[] = [], kwargs: Record<string, unknown> = {}): Promise<T> {
  const client = getReadClient();
  return client.readContract({
    address: PRIMACY_CONTRACT_ADDRESS,
    functionName,
    args,
    kwargs,
  }) as Promise<T>;
}

export const primacyReads = {
  getConstitution: () => readView<Constitution>("get_constitution"),
  getConfig: () => readView<Record<string, unknown>>("get_config"),
  getLanes: () => readView<Record<Lane, string[]>>("get_lanes"),
  getLane: (laneId: Lane) => readView<string[]>("get_lane", [], { lane_id: laneId }),
  getMarket: (marketId: number) => readView<MarketView>("get_market", [], { market_id: marketId }),
  getMarkets: (cursor: number, limit: number, stateFilter: string) =>
    readView<MarketView[]>("get_markets", [], { cursor, limit, state_filter: stateFilter }),
  getOpenMarkets: (cursor: number, limit: number) =>
    readView<MarketView[]>("get_open_markets", [], { cursor, limit }),
  getBoard: () => readView<MarketView[]>("get_board"),
  getBettingState: (marketId: number, address: string) =>
    readView<Record<string, unknown>>("get_betting_state", [], { market_id: marketId, address }),
  getUserPosition: (marketId: number, address: string) =>
    readView<Record<string, unknown>>("get_user_position", [], { market_id: marketId, address }),
  getUserPositions: (address: string, cursor: number, limit: number) =>
    readView<Record<string, unknown>[]>("get_user_positions", [], { address, cursor, limit }),
  getClaimableMarkets: (address: string, cursor: number, limit: number) =>
    readView<MarketView[]>("get_claimable_markets", [], { address, cursor, limit }),
  getSourceEvidence: (marketId: number) =>
    readView<Record<string, unknown>>("get_source_evidence", [], { market_id: marketId }),
  getUserActivity: (address: string, cursor: number, limit: number) =>
    readView<Record<string, unknown>[]>("get_user_activity", [], { address, cursor, limit }),
  getKeeperStats: () => readView<Record<string, unknown>>("get_keeper_stats"),
  getMarketByLaneStart: (laneId: Lane, start: number) =>
    readView<number | null>("get_market_by_lane_start", [], { lane_id: laneId, start }),
};

/**
 * Write client factory -- takes the wallet's REAL provider, obtained via
 * `await connector.getProvider()` from wagmi's `useAccount()` in the
 * eventual frontend, never `window.ethereum` directly (misses
 * WalletConnect/Coinbase Smart Wallet/Safe). Not memoized: the
 * account/provider pair changes whenever the connected wallet changes.
 */
export function createPrimacyWriteClient(account: string, provider: unknown) {
  return createClient({ chain: PRIMACY_CHAIN, account: account as `0x${string}`, provider: provider as never });
}

export interface PrimacyWriteClient {
  writeContract(args: {
    functionName: string;
    args?: unknown[];
    kwargs?: Record<string, unknown>;
    value?: bigint;
  }): Promise<string>;
}

export const primacyWrites = {
  createMarket: (client: PrimacyWriteClient, laneId: Lane, start: number, valueWei: bigint) =>
    client.writeContract({ functionName: "create_market", kwargs: { lane_id: laneId, start }, value: valueWei }),
  placeBet: (client: PrimacyWriteClient, marketId: number, symbol: string, valueWei: bigint) =>
    client.writeContract({ functionName: "place_bet", kwargs: { market_id: marketId, symbol }, value: valueWei }),
  settleMarket: (client: PrimacyWriteClient, marketId: number, valueWei: bigint) =>
    client.writeContract({ functionName: "settle_market", kwargs: { market_id: marketId }, value: valueWei }),
  claim: (client: PrimacyWriteClient, marketId: number) =>
    client.writeContract({ functionName: "claim", kwargs: { market_id: marketId }, value: 0n }),
  claimRefund: (client: PrimacyWriteClient, marketId: number) =>
    client.writeContract({ functionName: "claim_refund", kwargs: { market_id: marketId }, value: 0n }),
  reclaimBonds: (client: PrimacyWriteClient, marketId: number) =>
    client.writeContract({ functionName: "reclaim_bonds", kwargs: { market_id: marketId }, value: 0n }),
};

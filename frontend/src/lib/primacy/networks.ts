/**
 * Locked to GenLayer Studio Dev (Studio Next) -- chain 61997 only.
 * Never studionet (61999), never Bradbury (4221). See README.md section 9.
 */
import { studioDevnet } from "genlayer-js/chains";
import type { Chain } from "genlayer-js/types";

export const PRIMACY_CHAIN: Chain = studioDevnet;
export const PRIMACY_CHAIN_ID = 61997;
export const PRIMACY_RPC_URL = "https://studio-dev.genlayer.com/api";
export const PRIMACY_EXPLORER_BASE = "https://explorer-studio-dev.genlayer.com";

export const PRIMACY_CONTRACT_ADDRESS =
  process.env.NEXT_PUBLIC_CONTRACT_ADDRESS ?? "";

export function explorerAddressUrl(address: string): string {
  return `${PRIMACY_EXPLORER_BASE}/address/${address}`;
}

export function explorerTxUrl(hash: string): string {
  return `${PRIMACY_EXPLORER_BASE}/tx/${hash}`;
}

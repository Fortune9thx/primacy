/**
 * GEN <-> wei, hour labels (always UTC -- never local time, since the
 * contract's hour boundaries are Unix-second UTC), and state enums.
 */

const GEN_DECIMALS = 18n;
const GEN_SCALE = 10n ** GEN_DECIMALS;

export function weiToGen(wei: bigint | string | number): string {
  const v = typeof wei === "bigint" ? wei : BigInt(wei);
  const whole = v / GEN_SCALE;
  const frac = v % GEN_SCALE;
  if (frac === 0n) return whole.toString();
  const fracStr = frac.toString().padStart(18, "0").replace(/0+$/, "");
  return `${whole}.${fracStr}`;
}

export function genToWei(gen: string | number): bigint {
  const s = String(gen);
  const [wholeRaw, fracRaw = ""] = s.split(".");
  const whole = wholeRaw === "" ? 0n : BigInt(wholeRaw);
  const frac = (fracRaw + "0".repeat(18)).slice(0, 18);
  return whole * GEN_SCALE + BigInt(frac || "0");
}

/** Format a Unix-second hour boundary as a stable, explicit UTC label. */
export function formatHourUtc(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000);
  return d.toISOString().replace(":00.000Z", "Z").replace("T", " ") + " UTC";
}

export type MarketState = "OPEN" | "SETTLED" | "INCONCLUSIVE";

export const MARKET_STATE_LABELS: Record<MarketState, string> = {
  OPEN: "Open",
  SETTLED: "Settled",
  INCONCLUSIVE: "Inconclusive (refundable)",
};

export function isTerminalState(state: string): state is "SETTLED" | "INCONCLUSIVE" {
  return state === "SETTLED" || state === "INCONCLUSIVE";
}

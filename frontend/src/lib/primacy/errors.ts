/**
 * Maps Primacy.py's stable, lowercase UserError strings to a short,
 * human-readable message. Contract error text arrives wrapped in
 * platform-specific framing (varies by SDK version/call path) -- always
 * match by substring, never exact-equality, against the raw error text.
 */
export const PRIMACY_ERROR_MESSAGES: Record<string, string> = {
  "market not found": "That market doesn't exist.",
  "betting closed": "Betting has closed for this market.",
  "below min lead": "This market's start time is too close to now (needs at least 30 minutes' notice).",
  "side locked": "You already have a position on a different symbol in this market.",
  "below min bet": "Bets must be at least 1 GEN.",
  "unknown lane": "That isn't a recognized market lane.",
  "unknown symbol": "That symbol isn't part of this market's lane.",
  "not hour boundary": "The start time must land exactly on an hour boundary (UTC).",
  "already settled": "This market has already been settled.",
  "not expired": "This market's hour hasn't completed yet.",
  "nothing to claim": "There's nothing for you to claim on this market.",
  "not inconclusive": "This market isn't in a refundable (inconclusive) state.",
  "duplicate market": "A market for this lane and start time already exists.",
  "creator cap reached": "You already have the maximum number of open markets (8).",
  "wrong bond amount": "The attached GEN doesn't match the required bond amount.",
};

export function describePrimacyError(raw: string): string {
  const lower = raw.toLowerCase();
  for (const [key, message] of Object.entries(PRIMACY_ERROR_MESSAGES)) {
    if (lower.includes(key)) return message;
  }
  return raw;
}

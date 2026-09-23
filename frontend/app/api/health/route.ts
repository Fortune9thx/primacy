import { PRIMACY_CHAIN_ID, PRIMACY_CONTRACT_ADDRESS, PRIMACY_RPC_URL } from "@/lib/primacy/networks";
import { getReadClient } from "@/lib/primacy/primacyClient";

export async function GET() {
  const result: Record<string, unknown> = {
    rpc: PRIMACY_RPC_URL,
    chainId: PRIMACY_CHAIN_ID,
    contract: PRIMACY_CONTRACT_ADDRESS || null,
    codePresent: null,
    lastError: null,
  };

  if (!PRIMACY_CONTRACT_ADDRESS) {
    result.lastError = "NEXT_PUBLIC_CONTRACT_ADDRESS is not set";
    return Response.json(result, { status: 503 });
  }

  try {
    const client = getReadClient();
    const constitution = await client.readContract({
      address: PRIMACY_CONTRACT_ADDRESS,
      functionName: "get_constitution",
      args: [],
    });
    result.codePresent = Boolean(constitution);
    return Response.json(result);
  } catch (err) {
    result.codePresent = false;
    result.lastError = err instanceof Error ? err.message : String(err);
    return Response.json(result, { status: 503 });
  }
}

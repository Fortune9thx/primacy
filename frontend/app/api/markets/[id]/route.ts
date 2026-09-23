import { primacyReads } from "@/lib/primacy/primacyClient";

export async function GET(_req: Request, { params }: { params: { id: string } }) {
  const marketId = Number(params.id);
  if (!Number.isInteger(marketId) || marketId < 1) {
    return Response.json({ error: "invalid market id" }, { status: 400 });
  }
  try {
    const market = await primacyReads.getMarket(marketId);
    return Response.json(market);
  } catch (err) {
    return Response.json({ error: err instanceof Error ? err.message : String(err) }, { status: 502 });
  }
}

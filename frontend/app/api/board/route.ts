import { primacyReads } from "@/lib/primacy/primacyClient";

export async function GET() {
  try {
    const board = await primacyReads.getBoard();
    return Response.json(board);
  } catch (err) {
    return Response.json({ error: err instanceof Error ? err.message : String(err) }, { status: 502 });
  }
}

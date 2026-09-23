import { primacyReads } from "@/lib/primacy/primacyClient";

export async function GET() {
  try {
    const constitution = await primacyReads.getConstitution();
    return Response.json(constitution);
  } catch (err) {
    return Response.json({ error: err instanceof Error ? err.message : String(err) }, { status: 502 });
  }
}

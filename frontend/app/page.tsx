/**
 * Placeholder only -- visual design is a separate, later Lovable pass
 * against this contract's ABI (see README.md section 15). This route
 * exists so the app builds and so /api/health has something to sit
 * beside; it is not the product's UI.
 */
export default function HealthPage() {
  return (
    <main style={{ fontFamily: "monospace", padding: "2rem" }}>
      <h1>PRIMACY</h1>
      <p>Studio Next · chain 61997 · state may reset</p>
      <p>
        API: <a href="/api/health">/api/health</a>,{" "}
        <a href="/api/constitution">/api/constitution</a>,{" "}
        <a href="/api/board">/api/board</a>
      </p>
    </main>
  );
}

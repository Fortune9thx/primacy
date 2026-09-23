export const metadata = {
  title: "PRIMACY",
  description: "Studio Next · chain 61997 · state may reset",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

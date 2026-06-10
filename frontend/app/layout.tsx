import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'TigerGraph Savanna — Design Schema',
  description: 'AI-assisted schema design for TigerGraph',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

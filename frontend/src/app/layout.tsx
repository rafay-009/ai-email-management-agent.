import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "InboxPilot AI",
  description:
    "An inbox workspace for reviewing email, drafts, and follow-ups.",
};

export default function Layout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

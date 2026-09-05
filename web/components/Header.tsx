"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/models", label: "My models" },
  { href: "/submit", label: "New submission" },
] as const;

export function Header() {
  const pathname = usePathname();

  return (
    <header className="site-header">
      <div className="shell header-inner">
        <Link href="/models" className="brand" aria-label="Weightroom Seller Studio home">
          <strong>Weightroom</strong>
          <span>Seller Studio</span>
        </Link>
        <nav aria-label="Seller navigation">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="nav-link"
              aria-current={pathname.startsWith(link.href) ? "page" : undefined}
            >
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="session-indicator" title="Authentication is handled by the secure server session">
          <span aria-hidden="true" />
          Seller account
        </div>
      </div>
    </header>
  );
}

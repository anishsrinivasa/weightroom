"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/buy", label: "Buy models" },
  { href: "/sell/models", label: "My models" },
  { href: "/sell/submit", label: "New submission" },
] as const;

export function Header() {
  const pathname = usePathname();

  return (
    <header className="site-header">
      <div className="shell header-inner">
        <Link href="/buy" className="brand" aria-label="Weightroom home">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="brand-mark" src="/logo.png" alt="" width={26} height={26} />
          <strong>Weightroom</strong>
        </Link>
        <nav aria-label="Marketplace navigation">
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
      </div>
    </header>
  );
}

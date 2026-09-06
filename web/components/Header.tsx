"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/buy", label: "Buy models", shortLabel: "Buy" },
  { href: "/sell/models", label: "My models", shortLabel: "Sell" },
  { href: "/blog", label: "Research", shortLabel: "Research" },
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
              <span className="nav-label-full">{link.label}</span>
              <span className="nav-label-short">{link.shortLabel}</span>
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}

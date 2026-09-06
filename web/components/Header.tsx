"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  SignedIn,
  SignedOut,
  SignInButton,
  SignUpButton,
  UserButton,
} from "@clerk/nextjs";

const links = [
  { href: "/buy", label: "Buy models", shortLabel: "Buy" },
  { href: "/sell/models", label: "My models", shortLabel: "Sell" },
  { href: "/blog", label: "Blog", shortLabel: "Blog" },
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
        {/* Listing, buying and voting all require an account, so the way to
            get one is in the chrome rather than discovered at the point of
            being refused. */}
        <div className="header-account">
          <SignedOut>
            <SignInButton mode="modal">
              <button className="text-button" type="button">Sign in</button>
            </SignInButton>
            <SignUpButton mode="modal">
              <button className="button" type="button">Create account</button>
            </SignUpButton>
          </SignedOut>
          <SignedIn>
            <UserButton />
          </SignedIn>
        </div>
      </div>
    </header>
  );
}

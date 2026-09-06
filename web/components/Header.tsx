"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { UserButton, useAuth } from "@clerk/nextjs";

const links = [
  { href: "/buy", label: "Buy models", shortLabel: "Buy" },
  { href: "/sell/models", label: "My models", shortLabel: "Sell" },
  { href: "/blog", label: "Research", shortLabel: "Research" },
] as const;

export function Header() {
  const pathname = usePathname();
  // `<SignedIn>` / `<SignedOut>` were removed in Clerk's Core 3 and now throw
  // the moment they render. They still export, still typecheck and still
  // build, so nothing catches them until a real request arrives -- which took
  // every rendered page down while the API kept answering. `useAuth` is the
  // supported replacement.
  const { isLoaded, isSignedIn } = useAuth();

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
            being refused.

            Nothing renders until Clerk has loaded: showing "Account" for the
            moment before the state arrives would read, to someone already
            signed in, as having been signed out. */}
        <div className="header-account">
          {!isLoaded ? null : isSignedIn ? (
            <UserButton />
          ) : (
            <Link className="button" href="/account">Account</Link>
          )}
        </div>
      </div>
    </header>
  );
}

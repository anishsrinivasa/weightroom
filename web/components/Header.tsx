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
          <img className="brand-mark" src="/logo.png" alt="" width={36} height={36} />
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

            The link is the default until Clerk reports otherwise, so it is
            what the server renders and what someone with no JavaScript gets.
            Rendering nothing while loading left the slot empty in the HTML,
            which is a worse answer than the correct one for a visitor who is
            not signed in -- and a signed-in user sees it swap to their avatar
            on hydration rather than being told anything false. */}
        <div className="header-account">
          {isLoaded && isSignedIn ? (
            <UserButton />
          ) : (
            <Link className="button" href={{ pathname: "/account/[[...rest]]" }}>Account</Link>
          )}
        </div>
      </div>
    </header>
  );
}

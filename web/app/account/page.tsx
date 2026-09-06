"use client";

import { SignIn, UserProfile, useAuth } from "@clerk/nextjs";

/**
 * A page rather than a modal. Signing in is a destination you can link to,
 * bookmark, and be redirected back to when an action needs an account -- a
 * modal is none of those, and it leaves the page behind it half-usable.
 *
 * Client-side because the signed-in branch is decided by `useAuth`. The
 * `<SignedIn>` / `<SignedOut>` components that would have done this on the
 * server were removed in Clerk's Core 3 and throw when rendered.
 */
export default function AccountPage() {
  const { isLoaded, isSignedIn } = useAuth();

  if (!isLoaded) {
    return (
      <section className="account-page" aria-label="Account">
        <p className="account-lede">Loading your account…</p>
      </section>
    );
  }

  return (
    <section className="account-page" aria-label="Account">
      {isSignedIn ? (
        <UserProfile
          routing="hash"
          appearance={{ elements: { rootBox: "account-clerk-root" } }}
        />
      ) : (
        <>
          <p className="account-lede">
            An account is needed to list a model, buy one, or vote.
          </p>
          {/* Which sign-in methods appear -- email, password, Google -- is
              configured on the Clerk instance, so enabling another provider
              later needs no change here. */}
          <SignIn
            routing="hash"
            appearance={{ elements: { rootBox: "account-clerk-root" } }}
          />
        </>
      )}
    </section>
  );
}

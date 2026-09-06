"use client";

import { SignIn, UserProfile, useAuth } from "@clerk/nextjs";

/**
 * A catch-all, not a single page. OAuth does not come back to where it
 * started: Clerk needs real path segments beneath this route to finish a
 * redirect flow -- `/account/sso-callback` and the factor steps after it.
 * With hash routing there is nowhere for that to land, so signing in with
 * Google returned to a card that span forever.
 *
 * A page rather than a modal for the same reason as before: signing in is a
 * destination you can link to, bookmark, and be sent back to when an action
 * needs an account.
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
          path="/account"
          routing="path"
          appearance={{ elements: { rootBox: "account-clerk-root" } }}
        />
      ) : (
        <>
          <p className="account-lede">
            An account is needed to list a model, buy one, or vote.
          </p>
          {/* Which methods appear -- email, password, Google -- is configured
              on the Clerk instance, so enabling another provider later needs
              no change here. */}
          <SignIn
            path="/account"
            routing="path"
            signUpUrl="/account"
            fallbackRedirectUrl="/buy"
            appearance={{ elements: { rootBox: "account-clerk-root" } }}
          />
        </>
      )}
    </section>
  );
}

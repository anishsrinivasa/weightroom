import type { Metadata } from "next";
import { SignedIn, SignedOut, SignIn, UserProfile } from "@clerk/nextjs";

export const metadata: Metadata = {
  title: "Account",
  description: "Sign in to list, buy, and vote on models.",
};

/**
 * A page rather than a modal. Signing in is a destination you can link to,
 * bookmark, and be redirected back to when an action needs an account -- a
 * modal is none of those, and it leaves the page behind it half-usable.
 */
export default function AccountPage() {
  return (
    <section className="account-page" aria-label="Account">
      <SignedOut>
        <p className="account-lede">
          An account is needed to list a model, buy one, or vote.
        </p>
        {/* Clerk renders email/password and Google side by side; which methods
            appear is configured on the instance rather than here, so enabling
            another provider later needs no change in this file. */}
        <SignIn
          routing="hash"
          appearance={{
            elements: {
              rootBox: "account-clerk-root",
              cardBox: "account-clerk-card",
            },
          }}
        />
      </SignedOut>
      <SignedIn>
        <UserProfile
          routing="hash"
          appearance={{ elements: { rootBox: "account-clerk-root" } }}
        />
      </SignedIn>
    </section>
  );
}

import { SubmitWizard } from "./SubmitWizard";

export const metadata = { title: "Create a model" };

export default async function SubmitPage({
  searchParams,
}: {
  searchParams: Promise<{ draft?: string | string[] }>;
}) {
  const draft = (await searchParams).draft;
  return <SubmitWizard draftId={typeof draft === "string" ? draft : undefined} />;
}

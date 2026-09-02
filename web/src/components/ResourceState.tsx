import { ErrorState, LoadingState } from "./Feedback";

export function ResourceState({ resource }: { resource: { loading: boolean; error: string; refresh: () => void } }) {
  if (resource.loading) return <LoadingState />;
  if (resource.error) return <ErrorState message={resource.error} retry={resource.refresh} />;
  return null;
}

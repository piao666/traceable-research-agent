import { useOutletContext } from "react-router-dom";
import type { useRun } from "./useRun";

export type RunContext = ReturnType<typeof useRun>;
export function useRunContext() { return useOutletContext<RunContext>(); }

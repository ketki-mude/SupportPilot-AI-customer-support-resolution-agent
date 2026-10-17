import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { EvalLlm } from "@/lib/types";

export const evalKeys = {
  overview: ["evals", "overview"] as const,
  report: (id: string) => ["evals", "report", id] as const,
};

/** Saved runs and the run in progress, polled every two seconds while one is running. */
export function useEvalOverview() {
  return useQuery({
    queryKey: evalKeys.overview,
    queryFn: api.evalOverview,
    refetchInterval: (query) => (query.state.data?.current?.status === "running" ? 2_000 : false),
  });
}

export function useEvalReport(reportId: string | null) {
  return useQuery({
    queryKey: evalKeys.report(reportId ?? ""),
    queryFn: () => api.evalReport(reportId ?? ""),
    enabled: reportId !== null,
    placeholderData: (previous) => previous,
    staleTime: Infinity, // saved reports never change
  });
}

export function useStartEval() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ llm, limit }: { llm: EvalLlm; limit: number | null }) => api.startEval(llm, limit),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: evalKeys.overview }),
  });
}

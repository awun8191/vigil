import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import type { SkillResponse } from '@/types';

export const SKILLS_KEY = ['skills'] as const;

const isHexSha = /^[0-9a-f]{7,40}$/i;

export function useSkills() {
  return useQuery<SkillResponse[]>({
    queryKey: SKILLS_KEY,
    queryFn: () => api.listSkills(),
  });
}

/**
 * Installation is async on the backend: the row appears immediately with `enabled: false`
 * and the caller refreshes to see the final state. The client validates the SHA shape only,
 * providing early feedback, the backend is the authority.
 */
export function useInstallSkill() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: {
      repo_url: string;
      commit: string;
      scope: string;
    }) => {
      if (!isHexSha.test(input.commit)) {
        return Promise.reject(new Error('Commit must be a hexadecimal SHA'));
      }
      return api.installSkill({ repo_url: input.repo_url, commit: input.commit }, input.scope);
    },

    onSuccess: (skill: SkillResponse) => {
      queryClient.setQueryData<SkillResponse[]>(SKILLS_KEY, (current) =>
        current ? [...current, skill] : [skill],
      );
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: SKILLS_KEY });
    },
  });
}

/**
 * Toggling rides the backend's dedicated routes: `POST /skills/{id}/enable` and
 * `POST /skills/{id}/disable`, neither of which takes a body. The hook picks the route
 * from the desired state.
 */
export function useToggleSkill() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ skill, scope }: { skill: SkillResponse; scope: string }) =>
      skill.enabled ? api.disableSkill(skill.id, scope) : api.enableSkill(skill.id, scope),

    onSuccess: (updated: SkillResponse) => {
      queryClient.setQueryData<SkillResponse[]>(SKILLS_KEY, (current) =>
        current ? current.map((s) => (s.id === updated.id ? updated : s)) : [updated],
      );
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: SKILLS_KEY });
    },
  });
}

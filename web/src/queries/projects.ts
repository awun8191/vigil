import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import type {
  Project,
  ProjectCreateRequest,
  ProjectDetail,
  ProjectUpdateRequest,
} from '@/types';

export const PROJECTS_KEY = ['projects'] as const;
export const projectDetailKey = (projectId: string) => ['projects', projectId] as const;

export function useProjects() {
  return useQuery<Project[]>({
    queryKey: PROJECTS_KEY,
    queryFn: () => api.listProjects(),
  });
}

export function useProjectDetail(projectId: string | null) {
  return useQuery<ProjectDetail>({
    queryKey: projectDetailKey(projectId ?? 'none'),
    queryFn: () => api.getProject(projectId as string),
    enabled: Boolean(projectId),
  });
}

export function useCreateProject() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ body, scope }: { body: ProjectCreateRequest; scope: string }) =>
      api.createProject(body, scope),
    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: PROJECTS_KEY });
    },
  });
}

export function useUpdateProject() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      projectId,
      body,
      scope,
    }: {
      projectId: string;
      body: ProjectUpdateRequest;
      scope: string;
    }) => api.updateProject(projectId, body, scope),
    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: PROJECTS_KEY });
      void queryClient.invalidateQueries({
        queryKey: projectDetailKey(variables.projectId),
      });
    },
  });
}

export function useDeleteProject() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ projectId, scope }: { projectId: string; scope: string }) =>
      api.deleteProject(projectId, scope),
    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: PROJECTS_KEY });
    },
  });
}

export function useUploadProjectFiles() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ projectId, files }: { projectId: string; files: File[] }) =>
      api.uploadProjectFiles(projectId, files),
    onSettled: (_data, _error, variables) => {
      void queryClient.invalidateQueries({
        queryKey: projectDetailKey(variables.projectId),
      });
    },
  });
}

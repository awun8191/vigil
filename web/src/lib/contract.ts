import type {
  AgentConfigResponse,
  Approval,
  ApprovalDecisionRequest,
  ApprovalDecisionResponse,
  Artifact,
  AuthMe,
  CardFeedbackRequest,
  CardListResponse,
  Card,
  ChatMessage,
  ChatSession,
  ConnectorAccount,
  HealthStatus,
  MCPDiscoveryResponse,
  MCPServerResponse,
  MCPToolResponse,
  PairingResponse,
  Project,
  ProjectCreateRequest,
  ProjectDetail,
  ProjectFile,
  ProjectUpdateRequest,
  RegisterMCPServerRequest,
  UpdateMCPToolLevelRequest,
  Run,
  RunCancelResponse,
  RunCreateRequest,
  RunMessageResponse,
  RunStep,
  RunUploadResponse,
  SseEvent,
  SkillInstallRequest,
  SkillResponse,
  StreamState,
  SyncTrigger,
  SystemLimits,
  UserSettings,
  UserSettingsUpdateRequest,
  VaultSecretDTO,
  SetVaultSecretRequest,
  VaultValidationResult,
  PersonalizationResponse,
  PersonalizationUpdateRequest,
  MemoryChunkListResponse,
  MemorySearchResponse,
  Widget,
} from '@/types';


export interface StreamHandlers {
  onEvent: (event: SseEvent) => void;
  onStateChange?: (state: StreamState) => void;
}

/**
 * The surface the UI codes against. `lib/api.ts` provides the real implementation,
 * `lib/mock.ts` provides the fixture-backed one. The two are interchangeable and are selected
 * at build time by `VITE_VIGIL_MOCK`, never at runtime.
 */
export interface VigilApi {
  readonly mode: 'live' | 'mock';

  pair(pairingCode: string, deviceName: string): Promise<PairingResponse>;
  me(): Promise<AuthMe>;
  revokeDevice(deviceId: string): Promise<void>;

  listCards(params?: { limit?: number; offset?: number }): Promise<CardListResponse>;
  dismissCard(cardId: string, scope: string): Promise<Card>;
  cardFeedback(cardId: string, body: CardFeedbackRequest, scope: string): Promise<Card>;

  listWidgets(): Promise<Widget[]>;
  /** Delete one dashboard panel. The backend scopes the delete to the caller. */
  deleteWidget(widgetId: string, scope: string): Promise<void>;
  /**
   * Subscribe to `GET /widgets/stream`. Frames carry `event: widget_updated` with a
   * `WidgetUpdatedEvent` data payload and `id: <updated_at>` for `Last-Event-ID`
   * replay. One stream per dashboard, not one per widget. Returns an unsubscribe function.
   */
  openWidgetsStream(options: { handlers: StreamHandlers }): () => void;

  /**
   * Card actions with `action_type: 'run'` post here. The body is a full `RunCreateRequest`:
   * the backend requires `config_id` and rejects the request without it, and no backend
   * entity synthesizes `run` actions that carry one, so callers must guard on the payload
   * before dispatching.
   */
  createRun(body: RunCreateRequest, scope: string): Promise<Run>;
  uploadRunFiles(
    files: File[],
    opts?: { sessionId?: string | null; projectId?: string | null },
  ): Promise<RunUploadResponse>;
  getRun(runId: string): Promise<Run>;
  /** Newest run whose messages carry the session, for re-subscription after a reload. */
  getLatestRun(sessionId: string): Promise<Run | null>;
  getRunSteps(runId: string, sinceStep?: number): Promise<RunStep[]>;
  getRunMessages(runId: string): Promise<RunMessageResponse[]>;
  listRunArtifacts(runId: string): Promise<Artifact[]>;
  downloadArtifactBlob(artifactId: string): Promise<Blob>;
  getArtifactText(artifactId: string): Promise<string>;
  /** Resolves the pinned server-side default agent config, creating it on the first call. */
  getDefaultAgentConfig(): Promise<AgentConfigResponse>;
  cancelRun(runId: string, scope: string): Promise<RunCancelResponse>;

  /**
   * Subscribe to `GET /runs/{id}/events`. Frames carry `id: <step_number>`,
   * `event: <event_type>` and a JSON data payload (`RunStepEvent`), and the stream closes
   * after the run completes or fails. Returns an unsubscribe function.
   */
  openRunStream(
    runId: string,
    options: { sinceStep?: number; handlers: StreamHandlers },
  ): () => void;

  listApprovals(): Promise<Approval[]>;
  getApproval(approvalId: string): Promise<Approval>;
  decideApproval(
    approvalId: string,
    body: ApprovalDecisionRequest,
    scope: string,
  ): Promise<ApprovalDecisionResponse>;

  getPrimarySession(): Promise<ChatSession>;
  listSessions(): Promise<ChatSession[]>;
  createSession(title: string | undefined, scope: string): Promise<ChatSession>;
  listMessages(
    sessionId: string,
    opts?: { limit?: number; beforeId?: string },
  ): Promise<ChatMessage[]>;
  sendMessage(sessionId: string, content: string, scope: string): Promise<ChatMessage>;

  health(): Promise<HealthStatus>;
  getSettings(): Promise<UserSettings>;
  updateSettings(body: UserSettingsUpdateRequest, scope: string): Promise<UserSettings>;
  getLimits(): Promise<SystemLimits>;
  listConnectors(): Promise<ConnectorAccount[]>;
  syncConnector(accountId: string, scope: string): Promise<SyncTrigger>;
  authorizeGoogleConnector(connectorId?: string): Promise<{ authorization_url: string }>;
  deleteConnectorAccount(accountId: string, scope: string): Promise<void>;

  /* Skills. Mirrors the backend verbs: install and enable/disable are POST routes that take
     no toggle body, so the caller passes the desired state and the hook picks the route. */
  listSkills(): Promise<SkillResponse[]>;
  installSkill(body: SkillInstallRequest, scope: string): Promise<SkillResponse>;
  enableSkill(skillId: string, scope: string): Promise<SkillResponse>;
  disableSkill(skillId: string, scope: string): Promise<SkillResponse>;

  /* MCP servers. Registration returns 202 with a discovery job id; unregistration is DELETE
     by name and returns 204. */
  listMCPServers(): Promise<MCPServerResponse[]>;
  registerMCPServer(body: RegisterMCPServerRequest, scope: string): Promise<MCPDiscoveryResponse>;
  unregisterMCPServer(name: string, scope: string): Promise<void>;
  listMCPServerTools(name: string): Promise<MCPToolResponse[]>;
  updateMCPToolLevel(
    serverName: string,
    toolName: string,
    body: UpdateMCPToolLevelRequest,
    scope: string,
  ): Promise<MCPToolResponse>;
  refreshMCPServer(name: string, scope: string): Promise<MCPDiscoveryResponse>;

  /* Key Vault */
  listVaultSecrets(): Promise<VaultSecretDTO[]>;
  setVaultSecret(body: SetVaultSecretRequest, scope: string): Promise<{ ok: boolean; key: string }>;
  deleteVaultSecret(key: string, scope: string): Promise<void>;
  /** Check a key against its provider without storing it. */
  testVaultSecret(body: SetVaultSecretRequest, scope: string): Promise<VaultValidationResult>;
  /** Check an already-stored key against its provider. */
  validateVaultSecret(key: string, scope: string): Promise<VaultValidationResult>;

  /* Personalization & Hermes-style Memory (SOUL.md, USER.md, MEMORY.md) */
  getPersonalization(): Promise<PersonalizationResponse>;
  updatePersonalization(
    body: PersonalizationUpdateRequest,
    scope: string,
  ): Promise<PersonalizationResponse>;

  /* Long-term Semantic Memory Chunks */
  listMemoryChunks(params?: {
    limit?: number;
    offset?: number;
    include_excluded?: boolean;
  }): Promise<MemoryChunkListResponse>;
  searchMemory(query: string, topN?: number): Promise<MemorySearchResponse>;
  excludeMemoryChunk(
    chunkId: string,
    excluded: boolean,
    scope: string,
  ): Promise<{ excluded: boolean }>;
  deleteMemoryChunk(chunkId: string, scope: string): Promise<void>;

  /* Scoped Project Workspaces */
  listProjects(): Promise<Project[]>;
  createProject(body: ProjectCreateRequest, scope: string): Promise<Project>;
  getProject(projectId: string): Promise<ProjectDetail>;
  updateProject(
    projectId: string,
    body: ProjectUpdateRequest,
    scope: string,
  ): Promise<Project>;
  uploadProjectFiles(projectId: string, files: File[]): Promise<ProjectFile[]>;
  deleteProject(projectId: string, scope: string): Promise<void>;
}


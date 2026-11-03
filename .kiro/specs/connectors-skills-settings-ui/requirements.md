# Requirements Document

## Introduction

This feature adds a unified management interface for MCP servers and skills within the Vigil settings page. MCP servers are third-party integrations discovered via background jobs, while skills are agent capabilities installed from Git repositories. The interface consolidates these alongside existing connector accounts, providing users with a single view of all external integrations differentiated by type badges.

## Glossary

- **MCP_Server**: A third-party Model Context Protocol server that provides tools to the Vigil agent through stdio or SSE transport.
- **Skill**: An installed agent capability package sourced from a Git repository, pinned to a specific commit SHA.
- **Connector_Account**: An authenticated link to an external service (e.g., Linear, GitHub) that feeds data to Vigil.
- **Type_Badge**: A visual indicator distinguishing item types (connector account, MCP server, or skill) within the unified list.
- **Discovery_Job**: A background task that handshakes with an MCP server and stores it after successful tool enumeration.
- **Install_Status**: The current state of a skill installation (installing, installed, or failed).

## Requirements

### Requirement 1: Unified Connectors List with MCP Servers

**User Story:** As a Vigil user, I want to see my MCP servers alongside my connector accounts in a single list, so that I can manage all external integrations in one place.

#### Acceptance Criteria

1. THE System SHALL display connector accounts and MCP servers in a single unified list within the Connectors settings section.
2. THE System SHALL differentiate each list item with a type badge showing either "Connector", "MCP Server", or "Skill".
3. WHEN the user views the Connectors section, THE System SHALL render connector accounts with their existing status and refresh functionality.
4. WHEN the user views the Connectors section, THE System SHALL render MCP servers with their name, transport type, and enabled status.
5. WHEN the backend returns an empty array for the items endpoint, THE System SHALL display a "No items found" empty state for that account.

### Requirement 2: MCP Server Registration

**User Story:** As a Vigil user, I want to register a new MCP server by providing its configuration, so that its tools become available to the agent.

#### Acceptance Criteria

1. WHEN the user initiates MCP server registration, THE System SHALL display a form accepting name, transport type (stdio or sse), and either command (for stdio) or url (for sse).
2. WHEN the user submits the MCP server registration form, THE System SHALL POST to `/api/v1/connectors/mcp-servers` and receive a 202 Accepted response.
3. WHEN the registration request succeeds, THE System SHALL display the MCP server in the unified list with a "queued" discovery status.
4. IF an MCP server with the same name already exists, THE System SHALL display a conflict error message to the user.
5. WHEN discovery completes successfully, THE System SHALL update the MCP server status to enabled upon the next list refresh.

### Requirement 3: MCP Server Removal

**User Story:** As a Vigil user, I want to remove an MCP server I no longer need, so that its tools are no longer available to the agent.

#### Acceptance Criteria

1. WHEN the user triggers removal of an MCP server, THE System SHALL DELETE to `/api/v1/connectors/mcp-servers/{name}`.
2. WHEN the removal request succeeds, THE System SHALL remove the MCP server from the unified list immediately.
3. IF the MCP server does not exist, THE System SHALL display a not found error and refresh the list.

### Requirement 4: Skills Section with Full Management

**User Story:** As a Vigil user, I want a dedicated Skills section where I can install, view, enable, and disable skills, so that I can control which capabilities the agent has access to.

#### Acceptance Criteria

1. THE System SHALL add a "Skills" entry to the settings navigation between "Connectors" and "Usage".
2. WHEN the user navigates to the Skills section, THE System SHALL display all installed skills with their name, description, version, source, and enabled status.
3. WHEN the user clicks an enable/disable toggle on a skill, THE System SHALL PATCH to `/api/v1/skills/{id}` with the `enabled` field and reflect the change immediately.
4. WHEN the user refreshes the Skills section, THE System SHALL fetch the latest skill states from `/api/v1/skills`.

### Requirement 5: Skill Installation from Git

**User Story:** As a Vigil user, I want to install a new skill by providing its Git repository URL and commit SHA, so that the agent gains new capabilities pinned to a specific version.

#### Acceptance Criteria

1. WHEN the user initiates skill installation, THE System SHALL display a form accepting `repo_url` and `commit` fields.
2. WHEN the user submits the skill installation form, THE System SHALL POST to `/api/v1/skills` with the repository URL and commit SHA.
3. WHEN the installation request is accepted, THE System SHALL display the skill in the list with an "Installing..." status indicator.
4. THE System SHALL NOT block the UI waiting for installation completion; the user may refresh to check the final status.
5. IF the commit value is not a valid hexadecimal SHA, THE System SHALL display a validation error before submitting.

### Requirement 6: Type System and API Contract Updates

**User Story:** As a developer, I want the frontend types to match the backend schemas exactly, so that the UI correctly handles all API responses.

#### Acceptance Criteria

1. THE System SHALL add `SkillResponse`, `SkillInstallRequest`, and `SkillToggleRequest` types to `web/src/types/index.ts` matching the backend schemas.
2. THE System SHALL add `MCPServerResponse`, `RegisterMCPServerRequest`, and `MCPDiscoveryResponse` types to `web/src/types/index.ts` matching the backend schemas.
3. THE System SHALL remove `stale_thread_paused` from the `RunStatus` type union.
4. THE System SHALL extend the `VigilApi` interface in `web/src/lib/contract.ts` with methods for MCP server and skill management.
5. THE System SHALL implement the new API methods in `web/src/lib/api.ts` and provide fixture data in `web/src/lib/mock.ts`.

### Requirement 7: Query Hooks for Skills and MCP

**User Story:** As a developer, I want reusable React Query hooks for skills and MCP server operations, so that components can manage data fetching and mutations consistently.

#### Acceptance Criteria

1. THE System SHALL create `web/src/queries/skills.ts` with `useSkills`, `useInstallSkill`, and `useToggleSkill` hooks.
2. THE System SHALL extend `web/src/queries/settings.ts` with `useMCPServers`, `useRegisterMCPServer`, and `useUnregisterMCPServer` hooks.
3. WHEN a mutation hook succeeds, THE System SHALL invalidate the relevant query cache to trigger a refetch.
4. WHEN a mutation hook fails, THE System SHALL return the error for the component to display.

### Requirement 8: Skills Section UI Component

**User Story:** As a Vigil user, I want a clear and intuitive Skills section UI, so that I can quickly understand and manage my installed skills.

#### Acceptance Criteria

1. WHEN the Skills section loads, THE System SHALL display a loading state until skills data is available.
2. WHEN no skills are installed, THE System SHALL display an empty state explaining how to install a skill.
3. WHEN skills exist, THE System SHALL render each skill as a list item with name, description, version, source badge, and an enabled/disabled toggle.
4. WHEN a skill has `source: "git"`, THE System SHALL display the pinned commit SHA truncated to 7 characters.
5. WHEN a skill has `source: "builtin"`, THE System SHALL display a "Built-in" badge instead of commit information.

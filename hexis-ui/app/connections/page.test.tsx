import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ConnectionsPage from "./page";

const notionConnector = {
  id: "notion",
  display_name: "Notion",
  category: "productivity",
  auth_type: "api_key",
  status: "available",
  capability_manifest: {
    search: { label: "Search shared pages", scope_kind: "read", status: "available" },
    read: { label: "Read shared pages", scope_kind: "read", status: "available" },
    create: { label: "Create pages", scope_kind: "write", status: "available" },
  },
  setup_manifest: {
    default_capabilities: ["search", "read"],
    user_next_step: "Create a Notion integration and choose its token environment variable.",
    credential_fields: [
      {
        name: "token_env",
        label: "Notion token environment variable",
        secret: true,
        example: "NOTION_TOKEN",
      },
    ],
  },
  docs_url: "https://developers.notion.com/docs/create-a-notion-integration",
};

const statusPayload = {
  connectors: [notionConnector],
  connections: [],
  recent_attempts: [],
  channel_runtime: [],
  backfill: { jobs: [], cursors: [], item_counts: [] },
};

describe("Wave B connections setup", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("collects environment references, not secret values, and sends selected capabilities", async () => {
    const posts: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") {
          posts.push(JSON.parse(String(init.body)));
          return Response.json({
            success: true,
            output: {
              status: "connected",
              next_step: "Notion verified.",
              ui: { kind: "connector_setup", connector_id: "notion", status: "connected" },
            },
          });
        }
        return Response.json(statusPayload);
      }) as unknown as typeof fetch
    );

    render(<ConnectionsPage />);
    const envInput = await screen.findByLabelText("Notion token environment variable");
    expect(screen.getByText(/Environment variable name only/)).toBeInTheDocument();
    fireEvent.change(envInput, { target: { value: "MY_NOTION_TOKEN" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Create pages/ }));
    fireEvent.click(screen.getByRole("button", { name: "Verify Notion" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      action: "connect_life",
      arguments: {
        connector_id: "notion",
        token_env: "MY_NOTION_TOKEN",
        capabilities: ["search", "read", "create"],
      },
      source_session_id: "web-connections",
    });
    expect(JSON.stringify(posts[0])).not.toContain("secret-value");
  });

  it("displays standing action policies and audit entries and allows revoking a policy", async () => {
    const posts: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") {
          posts.push(JSON.parse(String(init.body)));
          return Response.json({
            success: true,
            display_output: "Connector action policy revoked.",
          });
        }
        return Response.json({
          ...statusPayload,
          action_policies: [
            {
              policy_id: "11111111-1111-1111-1111-111111111111",
              connector_id: "gmail",
              account_key: "eric@example.com",
              action_kind: "draft",
              status: "active",
              contexts: ["heartbeat"],
              allow_autonomous: true,
              requires_per_action_approval: false,
              constraints: { allowed_targets: ["recruiter@example.com"], max_per_day: 5 },
              rationale: "Stage polite declines for review",
              expires_at: null,
              created_at: "2026-11-21T00:00:00Z",
              updated_at: "2026-11-21T00:00:00Z",
            },
          ],
          action_audit: [
            {
              audit_id: "22222222-2222-2222-2222-222222222222",
              policy_id: "11111111-1111-1111-1111-111111111111",
              connector_id: "gmail",
              account_key: "eric@example.com",
              action_kind: "draft",
              tool_name: "gmail_draft",
              target: "recruiter@example.com",
              decision: "allowed",
              reason: "preauthorized_policy",
              sensitivity: "provider_state_change",
              created_at: "2026-11-21T01:00:00Z",
            },
          ],
        });
      }) as unknown as typeof fetch
    );

    render(<ConnectionsPage />);
    expect(await screen.findByText("Standing action policies")).toBeInTheDocument();
    expect(screen.getByText("Stage polite declines for review")).toBeInTheDocument();
    expect(screen.getByText(/Targets: recruiter@example.com/)).toBeInTheDocument();
    expect(screen.getByText(/gmail_draft/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Revoke policy" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/Revoke standing gmail draft policy/);
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      action: "revoke_action_policy",
      arguments: {
        policy_id: "11111111-1111-1111-1111-111111111111",
        reason: "revoked from web connections",
      },
      source_session_id: "web-connections",
    });
  });
});

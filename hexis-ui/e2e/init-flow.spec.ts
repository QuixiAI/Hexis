/**
 * The whole first-run journey, in a real browser: Models → Express → Consent
 * → Complete, with the dashboard, the Python API and a fresh database.
 *
 * Prerequisites (the same ones `hexis up` provides):
 *   - the compose `db` service running (COMPOSE_PROJECT_NAME / POSTGRES_* are
 *     honoured, so an isolated test project works: see tests in CI)
 *   - an embedding service the database can reach (the sidecar, or
 *     ops/ci/fake_embeddings.py on port 42666)
 *   - the Python venv at ../.venv (or HEXIS_PYTHON pointing at a python)
 *
 * Consent is mocked (HEXIS_CONSENT_MOCK=1), so no model is called. The API
 * runs with its own HOME, so the test's API key never touches ~/.hexis.
 */
import { expect, test } from "@playwright/test";
import { spawn, spawnSync } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";
import { PrismaClient } from "@prisma/client";
import { normalizeJsonValue } from "../lib/db";

const uiPort = Number(process.env.HEXIS_UI_PORT || 3479);
const apiPort = Number(process.env.HEXIS_E2E_API_PORT || 43819);
const repoRoot = path.resolve(__dirname, "../..");
const uiRoot = path.resolve(repoRoot, "hexis-ui");

const envConfig = { ...loadEnvConfig(repoRoot), ...process.env } as Record<string, string | undefined>;
// Compose defaults (docker-compose.yml), not generic Postgres ones.
const pgUser = envConfig.POSTGRES_USER || "hexis_user";
const pgPassword = envConfig.POSTGRES_PASSWORD || "hexis_password";
const pgPort = envConfig.POSTGRES_PORT || "43815";
const pgDatabase = envConfig.POSTGRES_DB || "hexis_memory";
const bindAddress = envConfig.HEXIS_BIND_ADDRESS || "127.0.0.1";
const pgHost = bindAddress === "0.0.0.0" ? "127.0.0.1" : bindAddress;
const dbName = `hexis_ui_e2e_${Date.now()}`;
const dbUrl = `postgresql://${encodeURIComponent(pgUser)}:${encodeURIComponent(
  pgPassword
)}@${pgHost}:${pgPort}/${dbName}`;
const pythonBin = process.env.HEXIS_PYTHON || path.join(repoRoot, ".venv", "bin", "python");

let uiProcess: ReturnType<typeof spawn> | null = null;
let apiProcess: ReturnType<typeof spawn> | null = null;
let prisma: PrismaClient | null = null;
let apiHome = "";
let logs = "";

test.beforeAll(async () => {
  await waitForPostgres();
  await waitForPostgresStable(pgDatabase);
  createDatabase(dbName);
  applySchema(dbName);
  await waitForEmbeddings(dbName);

  // The API owns model setup; its auth store lives under HOME, so give it one of its own.
  apiHome = fs.mkdtempSync(path.join(os.tmpdir(), "hexis-e2e-home-"));
  apiProcess = spawn(pythonBin, ["-m", "apps.hexis_api", "--port", String(apiPort)], {
    cwd: repoRoot,
    env: {
      ...process.env,
      HOME: apiHome,
      POSTGRES_HOST: pgHost,
      POSTGRES_PORT: pgPort,
      POSTGRES_USER: pgUser,
      POSTGRES_PASSWORD: pgPassword,
      POSTGRES_DB: dbName,
      HEXIS_CONSENT_MOCK: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  capture(apiProcess, "api");

  uiProcess = spawn(path.join(uiRoot, "node_modules", ".bin", "next"), ["dev", "-p", String(uiPort)], {
    cwd: uiRoot,
    env: {
      ...process.env,
      DATABASE_URL: dbUrl,
      HEXIS_DATABASE_URL: dbUrl,
      HEXIS_API_URL: `http://127.0.0.1:${apiPort}`,
      HEXIS_UI_PORT: String(uiPort),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  capture(uiProcess, "ui");

  await waitForServer(`http://127.0.0.1:${apiPort}/api/init/auth/status?provider=openai-codex`, 90000);
  await waitForServer(`http://127.0.0.1:${uiPort}/api/init/status`, 90000);

  prisma = new PrismaClient({ datasources: { db: { url: dbUrl } } });
});

test.afterAll(async () => {
  await prisma?.$disconnect();
  await stop(uiProcess);
  await stop(apiProcess);
  if (apiHome) fs.rmSync(apiHome, { recursive: true, force: true });
  dropDatabase(dbName);
});

test("sets up models, applies express defaults, gains consent, and persists it all", async ({ page }) => {
  await page.goto("/");

  // --- Models: one entry per vendor; OpenAI with an API key. ---
  await expect(page.getByText("Configure the models for the conscious and subconscious layers.")).toBeVisible();
  await expect(page.getByLabel("Provider")).toHaveValue("openai");
  // Codex is the login inside the OpenAI form, not a provider of its own.
  await expect(page.getByRole("option", { name: /Codex/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Log in with ChatGPT" })).toBeVisible();
  await page.getByRole("textbox", { name: "API key" }).fill("test-key");
  await page.getByRole("textbox", { name: "Model", exact: true }).fill("gpt-4o");
  await page.getByRole("button", { name: "Save Models" }).click();

  // --- Choose a path: Express. ---
  await expect(page.getByRole("button", { name: /Express/ })).toBeVisible();
  await page.getByRole("button", { name: /Express/ }).click();
  await page.getByLabel("What should Hexis call you?").fill("Erin");
  await page.getByRole("button", { name: "Go" }).click();

  // --- Consent: both models are asked; the mock answers yes. ---
  await expect(page.getByRole("button", { name: "Request Consent" })).toBeVisible();
  await page.getByRole("button", { name: "Request Consent" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("button", { name: "Enter Hexis" })).toBeVisible();

  // --- What the database and the auth store hold afterwards. ---
  expect(prisma).not.toBeNull();
  if (!prisma) return;

  const statusRows = await prisma.$queryRaw<{ status: unknown }[]>`SELECT get_init_status() as status`;
  const status = normalizeJsonValue(statusRows[0]?.status) as { stage?: string } | null;
  expect(status?.stage).toBe("complete");

  const configuredRows = await prisma.$queryRaw<{ configured: unknown }[]>`
    SELECT get_config('agent.is_configured') as configured
  `;
  expect(Boolean(normalizeJsonValue(configuredRows[0]?.configured))).toBe(true);

  const profileRows = await prisma.$queryRaw<{ profile: unknown }[]>`
    SELECT get_config('agent.init_profile') as profile
  `;
  const profile = normalizeJsonValue(profileRows[0]?.profile) as { user?: { name?: string } } | null;
  expect(profile?.user?.name).toBe("Erin");

  const consentRows = await prisma.$queryRaw<{ decision: string }[]>`
    SELECT decision FROM consent_log ORDER BY decided_at DESC LIMIT 1
  `;
  expect(consentRows[0]?.decision).toBe("consent");

  // The key went to the API's auth store; the config holds only a reference.
  const llmRows = await prisma.$queryRaw<{ llm: unknown }[]>`SELECT get_config('llm.chat') as llm`;
  const llm = normalizeJsonValue(llmRows[0]?.llm) as Record<string, unknown> | null;
  expect(llm).toMatchObject({ provider: "openai", model: "gpt-4o", api_key_ref: "llm.conscious" });
  expect(llm).not.toHaveProperty("api_key");
  const stored = JSON.parse(fs.readFileSync(path.join(apiHome, ".hexis", "auth", "llm.conscious.json"), "utf-8"));
  expect(stored.api_key).toBe("test-key");
  for (const envFile of [path.join(repoRoot, ".env"), path.join(uiRoot, ".env.local")]) {
    if (fs.existsSync(envFile)) expect(fs.readFileSync(envFile, "utf-8")).not.toContain("test-key");
  }
});

function capture(proc: ReturnType<typeof spawn>, name: string) {
  proc.stdout?.on("data", (chunk) => { logs += `[${name}] ${chunk}`; });
  proc.stderr?.on("data", (chunk) => { logs += `[${name}] ${chunk}`; });
}

async function stop(proc: ReturnType<typeof spawn> | null) {
  if (!proc) return;
  proc.kill("SIGTERM");
  await new Promise((resolve) => {
    proc.once("exit", resolve);
    setTimeout(resolve, 5000);
  });
}

function loadEnvConfig(rootDir: string) {
  const envPath = [".env.local", ".env"].map((name) => path.join(rootDir, name)).find(fs.existsSync);
  if (!envPath) {
    return {} as Record<string, string>;
  }
  const content = fs.readFileSync(envPath, "utf-8");
  const entries: Record<string, string> = {};
  for (const rawLine of content.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const idx = line.indexOf("=");
    if (idx <= 0) continue;
    const key = line.slice(0, idx).trim();
    let value = line.slice(idx + 1).trim();
    if ((value.startsWith("\"") && value.endsWith("\"")) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    entries[key] = value;
  }
  return entries;
}

function composeExecDb(args: string[], input?: string) {
  // COMPOSE_PROJECT_NAME in the environment selects the compose project.
  return spawnSync("docker", ["compose", "exec", "-T", "db", ...args], {
    cwd: repoRoot,
    encoding: "utf-8",
    input,
  });
}

async function waitForPostgres(timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (composeExecDb(["pg_isready", "-U", pgUser]).status === 0) return;
    await delay(1000);
  }
  throw new Error("Postgres did not become ready in time. Is the compose db service running?");
}

async function waitForPostgresStable(database: string, timeoutMs = 120000) {
  const start = Date.now();
  let stableCount = 0;
  let lastStart = "";
  while (Date.now() - start < timeoutMs) {
    const result = composeExecDb([
      "psql", "-U", pgUser, "-d", database, "-t", "-A", "-c", "SELECT pg_postmaster_start_time()::text;",
    ]);
    if (result.status === 0) {
      const currentStart = result.stdout.trim();
      if (currentStart && currentStart === lastStart) stableCount += 1;
      else { stableCount = 0; lastStart = currentStart; }
      if (stableCount >= 3) return;
    }
    await delay(1000);
  }
  throw new Error("Postgres did not reach a stable ready state.");
}

// The database embeds memories itself; ask it, rather than any one container,
// whether its embedding service answers.
async function waitForEmbeddings(database: string, timeoutMs = 120000) {
  const start = Date.now();
  let last = "";
  while (Date.now() - start < timeoutMs) {
    const result = composeExecDb([
      "psql", "-U", pgUser, "-d", database, "-t", "-A", "-c", "SELECT check_embedding_service_health();",
    ]);
    last = (result.stdout || result.stderr || "").trim();
    if (result.status === 0 && last.startsWith("t")) return;
    await delay(1000);
  }
  throw new Error(`The embedding service did not become ready in time (${last}).`);
}

function createDatabase(name: string) {
  runDockerPsql(`CREATE DATABASE ${name};`, pgDatabase);
}

function applySchema(name: string) {
  const schemaDir = path.resolve(repoRoot, "db");
  const schemaSql = fs
    .readdirSync(schemaDir)
    .filter((file) => file.endsWith(".sql"))
    .sort()
    .map((file) => fs.readFileSync(path.join(schemaDir, file), "utf-8"))
    .join("\n\n");
  const result = composeExecDb(["psql", "-U", pgUser, "-d", name, "-v", "ON_ERROR_STOP=1"], schemaSql);
  if (result.status !== 0) {
    throw new Error(`Schema apply failed: ${result.stderr || result.stdout}`);
  }
}

function dropDatabase(name: string) {
  try {
    runDockerPsql(`DROP DATABASE IF EXISTS ${name} WITH (FORCE);`, pgDatabase);
  } catch {
    // Ignore cleanup errors to avoid masking earlier failures.
  }
}

function runDockerPsql(sql: string, database: string) {
  const result = composeExecDb(["psql", "-U", pgUser, "-d", database, "-v", "ON_ERROR_STOP=1", "-t", "-A"], sql);
  if (result.status !== 0) {
    throw new Error(`psql failed: ${result.stderr || result.stdout}`);
  }
  return result.stdout.trim();
}

async function waitForServer(url: string, timeoutMs: number) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (res.ok) return;
    } catch {
      // not up yet
    }
    await delay(500);
  }
  throw new Error(`Server at ${url} did not start. Logs:\n${logs}`);
}

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

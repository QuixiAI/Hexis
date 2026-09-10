import { catalogDeclaredDefault, resolveInitLlmEndpoint } from "./init-llm";

describe("resolveInitLlmEndpoint", () => {
  it("drops stale API-key endpoints for Codex OAuth", () => {
    expect(
      resolveInitLlmEndpoint("openai-codex", "https://api.openai.com/v1")
    ).toBe("");
  });

  it("preserves user-controlled compatible endpoints", () => {
    expect(
      resolveInitLlmEndpoint("openai_compatible", "http://localhost:8000/v1")
    ).toBe("http://localhost:8000/v1");
  });

  it("derives the OpenAI API-key default", () => {
    expect(resolveInitLlmEndpoint("openai", "")).toBe(
      "https://api.openai.com/v1"
    );
  });

  it("derives the Atlas Cloud API-key default", () => {
    expect(resolveInitLlmEndpoint("atlascloud", "")).toBe(
      "https://api.atlascloud.ai/v1"
    );
  });

  it("derives the recommended model from live catalog metadata", () => {
    const block = {
      models: {
        preview: { id: "preview", description: "Limited preview model" },
        stable: { id: "stable", description: "Default frontier model" },
      },
    };
    expect(catalogDeclaredDefault(block, ["preview", "stable"])).toBe("stable");
    expect(catalogDeclaredDefault(block, ["preview"])).toBe("");
  });
});

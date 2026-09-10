import { describe, expect, it } from "vitest";

import { atlasChatModels } from "./route";

describe("atlasChatModels", () => {
  it("keeps only visible Atlas Cloud LLM models", () => {
    expect(
      atlasChatModels({
        data: [
          {
            model: "chat/model",
            type: "Text",
            categories: ["LLM"],
            display_console: true,
          },
          {
            model: "hidden/model",
            type: "Text",
            categories: ["LLM"],
            display_console: false,
          },
          {
            model: "image/model",
            type: "Image",
            categories: ["TEXT-TO-IMAGE"],
            display_console: true,
          },
        ],
      })
    ).toEqual(["chat/model"]);
  });
});

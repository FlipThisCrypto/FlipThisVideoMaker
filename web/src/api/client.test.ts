import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./client";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("api", () => {
  it("returns parsed JSON from the versioned API root", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
      );

    await expect(api<{ status: string }>("/health")).resolves.toEqual({
      status: "ok",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/health",
      expect.objectContaining({
        headers: expect.objectContaining({
          "Content-Type": "application/json",
        }),
      }),
    );
  });

  it("throws the response body for an unsuccessful request", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("project missing", { status: 404 }),
    );

    await expect(api("/projects/missing")).rejects.toThrow("project missing");
  });

  it("lets the browser set multipart boundaries for FormData", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ id: "asset" }), { status: 200 }),
      );
    const form = new FormData();
    form.append("file", new Blob(["image"]), "reference.png");

    await api("/characters/one/references", { method: "POST", body: form });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/characters/one/references",
      expect.objectContaining({ headers: {}, body: form }),
    );
  });
});

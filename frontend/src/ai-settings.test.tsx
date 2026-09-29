/**
 * The AI key card: a key goes in, and the page must never be able to show it.
 *
 * The backend refuses to return the key, so the browser only ever holds what the
 * user just typed. What has to be guarded here is the other half — that the
 * field is a password field, is emptied once saved, and that the saved state is
 * described from the server's hint rather than from anything typed.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const KEY = "sk-ant-api03-this-must-never-be-rendered";
const aiSettings = vi.fn();
const saveAiSettings = vi.fn();
const removeAiSettings = vi.fn();

vi.mock("./lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lib/api")>();
  return { ...actual, api: { ...actual.api, aiSettings, saveAiSettings, removeAiSettings } };
});

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  aiSettings.mockReset();
  saveAiSettings.mockReset();
  removeAiSettings.mockReset();
  container = document.createElement("div");
  document.body.appendChild(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

// A query resolves on a later tick than the render that asked for it, so wait
// for the queue to drain rather than assuming one act() is enough.
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

async function mount() {
  const { AiKeyCard } = await import("./main");
  root = createRoot(container);
  await act(async () => {
    root.render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AiKeyCard />
      </QueryClientProvider>,
    );
  });
  await settle();
}

function typeInto(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

const find = (selector: string) => container.querySelector(selector) as HTMLElement;
const buttonWith = (label: string) =>
  [...container.querySelectorAll("button")].find((b) => b.textContent?.includes(label));

test("the key field is a password field that does not invite autofill", async () => {
  aiSettings.mockResolvedValue({ configured: false });
  await mount();

  const field = find('input[type="password"]') as HTMLInputElement;
  expect(field).toBeTruthy();
  expect(field.autocomplete).toBe("new-password");
});

test("saving sends the key once, then empties the field", async () => {
  aiSettings.mockResolvedValue({ configured: false });
  saveAiSettings.mockResolvedValue({ configured: true });
  await mount();

  const field = find('input[type="password"]') as HTMLInputElement;
  const model = container.querySelector('input:not([type="password"])') as HTMLInputElement;
  await act(async () => typeInto(model, "claude-sonnet-5-5"));
  await act(async () => typeInto(field, KEY));
  await settle();
  await act(async () => {
    buttonWith("保存")!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await settle();

  expect(saveAiSettings).toHaveBeenCalledTimes(1);
  expect(saveAiSettings.mock.calls[0][1]).toBe(KEY);
  expect((find('input[type="password"]') as HTMLInputElement | null)?.value ?? "").toBe("");
  expect(container.textContent).not.toContain(KEY);
});

test("a saved key is described by the server's hint, not by anything typed", async () => {
  aiSettings.mockResolvedValue({
    configured: true,
    provider: "anthropic",
    model: "claude-sonnet-5-5",
    key_hint: "…7f3a",
  });
  await mount();

  expect(container.textContent).toContain("…7f3a");
  expect(container.textContent).toContain("claude-sonnet-5-5");
  expect(container.textContent).not.toContain("sk-");
});

test("removing asks the server to forget the key", async () => {
  aiSettings.mockResolvedValue({ configured: true, provider: "anthropic", model: "m", key_hint: "…abcd" });
  removeAiSettings.mockResolvedValue(undefined);
  await mount();

  await act(async () => {
    buttonWith("移除")!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });

  expect(removeAiSettings).toHaveBeenCalledTimes(1);
});

test("DeepSeek is offered and a saved DeepSeek key is labelled as such", async () => {
  aiSettings.mockResolvedValue({
    configured: true,
    provider: "deepseek",
    model: "some-model",
    key_hint: "…5678",
  });
  await mount();

  const options = Array.from(container.querySelectorAll("option")).map((option) => option.value);
  expect(options).toContain("deepseek");
  expect(container.querySelector(".connection-card strong")?.textContent).toBe("DeepSeek");
});

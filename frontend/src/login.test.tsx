/**
 * The magic link is single-use, so whoever opens it first spends it.
 *
 * Microsoft 365 Safe Links opens every URL it delivers in a sandbox that runs
 * the page's JavaScript. When this page verified on load, that sandbox claimed
 * the token and the student who clicked afterwards got "invalid or expired" —
 * for every recipient on a Microsoft-hosted domain, which is the whole intended
 * audience. Rendering must therefore stay inert until someone presses the
 * button, since a scanner renders but does not click.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

// React only treats act() as a real flush boundary when this is set.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const verifyMagicLink = vi.fn(() => Promise.resolve({}));
// Only the call under test is replaced; main.tsx reaches for the rest of the
// module as it loads.
vi.mock("./lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lib/api")>();
  return { ...actual, api: { ...actual.api, verifyMagicLink } };
});

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  verifyMagicLink.mockClear();
  container = document.createElement("div");
  document.body.appendChild(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  window.history.replaceState({}, "", "/");
});

async function renderLoginWithToken() {
  window.history.replaceState({}, "", "/?magic_token=token-from-the-email");
  const { LoginPage } = await import("./main");
  root = createRoot(container);
  await act(async () => {
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <LoginPage onAuthenticated={() => {}} />
      </QueryClientProvider>,
    );
  });
}

test("opening the link does not spend the token on its own", async () => {
  await renderLoginWithToken();

  expect(verifyMagicLink).not.toHaveBeenCalled();
});

test("pressing the button spends it", async () => {
  await renderLoginWithToken();
  const button = [...container.querySelectorAll("button")].find((element) =>
    element.textContent?.includes("确认登录"),
  );
  expect(button).toBeDefined();

  await act(async () => {
    button!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });

  expect(verifyMagicLink).toHaveBeenCalledWith("token-from-the-email");
});

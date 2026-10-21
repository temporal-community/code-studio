import { mount, unmount, flushSync } from "svelte";
import Login from "./Login.svelte";
import { sessionName } from "./shared/identity";
import App from "./App.svelte";
import Work from "./Work.svelte";
import Agents from "./Agents.svelte";
import Workspace from "./Workspace.svelte";
import FeatureBuilder from "./FeatureBuilder.svelte";
import ThemeToggle from "./shared/ThemeToggle.svelte";
import HarnessPip from "./shared/HarnessPip.svelte";
import { theme } from "./shared/theme";
import "./style.css";

theme.initialize();

async function start() {
  const target = document.getElementById("app")!;
  if (!sessionName()) {
    let login: ReturnType<typeof mount>;
    await new Promise<void>(resolve => {
      login = mount(Login, { target, props: { onjoin: resolve } });
    });
    await unmount(login!);
  }
  const view = new URLSearchParams(location.search).get("view");
  const component = view === "dag" ? App : view === "work" ? Work : view === "features" ? FeatureBuilder : ["studio", "review", "planner"].includes(view || "") ? Workspace : Agents;
  flushSync(() => mount(component, { target }));
  if (view !== "work") mount(HarnessPip, { target: document.getElementById("app")! });

  // Agents renders its toggle directly in its header. The other route components have
  // independent page shells, so mount the same shared control into each shell's header.
  const themeHostSelector = view === "dag" ? ".app > aside"
    : view === "work" ? ".work-app > .topbar"
      : view === "features" ? ".app-shell > .topbar"
        : ["studio", "review", "planner"].includes(view || "") ? ".workspace-app > header .header-actions"
          : null;
  if (themeHostSelector) {
    const target = document.querySelector(themeHostSelector);
    if (target) {
      const host = document.createElement("div");
      host.className = "theme-control-host";
      target.append(host);
      mount(ThemeToggle, { target: host });
    }
  }

}

void start();

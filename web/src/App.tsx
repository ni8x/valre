import { useEffect, useState } from "react";
import { api, Agent, LocalNames, Weapon } from "./api";
import Replays from "./pages/Replays";
import ReplayDetail from "./pages/ReplayDetail";
import Players from "./pages/Players";
import PlayerDetail from "./pages/PlayerDetail";
import DuoCheck from "./pages/DuoCheck";

export type Route =
  | { name: "list" }
  | { name: "players" }
  | { name: "detail"; id: string; tab: string }
  | { name: "player"; subject: string }
  | { name: "duo" }
  | { name: "duo-pair"; me: string; partner: string };

function parse(hash: string): Route {
  const h = hash.replace(/^#\/?/, "");
  const parts = h.split("/").filter(Boolean);
  if (parts[0] === "r" && parts[1]) {
    return { name: "detail", id: parts[1], tab: parts[2] || "match" };
  }
  if (parts[0] === "players") {
    return { name: "players" };
  }
  if (parts[0] === "p" && parts[1]) {
    return { name: "player", subject: decodeURIComponent(parts[1]) };
  }
  if (parts[0] === "duo" && parts[1] && parts[2]) {
    return { name: "duo-pair", me: decodeURIComponent(parts[1]), partner: decodeURIComponent(parts[2]) };
  }
  if (parts[0] === "duo") {
    return { name: "duo" };
  }
  return { name: "list" };
}

export default function App() {
  const [route, setRoute] = useState<Route>(parse(location.hash));
  const [agents, setAgents] = useState<Agent[]>([]);
  const [weapons, setWeapons] = useState<Weapon[]>([]);
  const [local, setLocal] = useState<LocalNames>({ me: null, names: {} });

  useEffect(() => {
    const on = () => setRoute(parse(location.hash));
    window.addEventListener("hashchange", on);
    if (!location.hash) location.hash = "#/";
    return () => window.removeEventListener("hashchange", on);
  }, []);

  useEffect(() => {
    api
      .agents()
      .then((list) => setAgents(list.map((a) => ({ ...a, uuid: (a.uuid || "").toLowerCase() }))))
      .catch(() => {});
    api
      .weapons()
      .then((list) => setWeapons(list))
      .catch(() => {});
  }, []);

  // Nicknames live in local settings and can change on the Players page, so this
  // refreshes on navigation instead of once at boot.
  useEffect(() => {
    api.playerNames().then(setLocal).catch(() => {});
  }, [route.name]);

  const go = (path: string) => {
    location.hash = path;
  };

  return (
    <div className="app">
      <a
        className="skip"
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        Skip to content
      </a>
      <header className={`topbar ${route.name === "list" ? "over-hero" : ""}`}>
        <a className="brand" href="#/" aria-label="VALRE home">
          <span className="brand-word">
            VAL<em>RE</em>
          </span>
          <span className="brand-sub">replay lab</span>
        </a>
        <nav aria-label="Main">
          <a href="#/" className={route.name === "list" ? "on" : ""}>
            History
          </a>
          <a href="#/players" className={route.name === "players" || route.name === "player" ? "on" : ""}>
            Players
          </a>
          <a href="#/duo" className={route.name === "duo" || route.name === "duo-pair" ? "on" : ""}>
            Duo check
          </a>
        </nav>
      </header>
      <main id="main" tabIndex={-1}>
        {route.name === "list" && <Replays agents={agents} me={local.me} names={local.names} />}
        {route.name === "players" && <Players agents={agents} />}
        {route.name === "detail" && (
          <ReplayDetail
            id={route.id}
            tab={route.tab}
            agents={agents}
            weapons={weapons}
            me={local.me}
            names={local.names}
          />
        )}
        {route.name === "player" && (
          <PlayerDetail subject={route.subject} agents={agents} weapons={weapons} go={go} />
        )}
        {route.name === "duo" && <DuoCheck go={go} agents={agents} />}
        {route.name === "duo-pair" && (
          <DuoCheck me={route.me} partner={route.partner} go={go} agents={agents} />
        )}
      </main>
      <footer className="foot">
        <span>VALRE keeps every replay and every number on this machine.</span>
        <span className="foot-note">Replays read with vrfkit · icons from valorant-api.com</span>
      </footer>
    </div>
  );
}

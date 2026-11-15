import { useState } from "react";
import { useTheme } from "./theme";
import Landing from "./screens/Landing";
import Connect from "./screens/Connect";
import Chat from "./screens/Chat";

type Screen = "landing" | "connect" | "chat";

export default function App() {
  const [screen, setScreen] = useState<Screen>("landing");
  const { theme, toggleTheme } = useTheme();

  if (screen === "connect") {
    return (
      <Connect
        theme={theme}
        onToggleTheme={toggleTheme}
        onBack={() => setScreen("landing")}
        onContinue={() => setScreen("chat")}
      />
    );
  }

  if (screen === "chat") {
    return <Chat theme={theme} onToggleTheme={toggleTheme} onHome={() => setScreen("landing")} />;
  }

  return (
    <Landing
      theme={theme}
      onToggleTheme={toggleTheme}
      onContinue={() => setScreen("connect")}
      onTrySample={() => setScreen("chat")}
    />
  );
}

import type { Theme } from "../theme";
import type { AccountProfile, ConnectorProvider } from "../lib/api";
import { Connectors } from "../components/Connectors";
import EngageSCPanel from "../EngageSCPanel";
import { Button, LockIcon, Logo, ThemeToggle } from "../components/ui";
import { CalendarConnection } from "../components/Calendar";

interface ConnectProps {
  selectedConnectors: ConnectorProvider[];
  onConnectorsChange: (providers: ConnectorProvider[]) => void;
  theme: Theme;
  onToggleTheme: () => void;
  onBack: () => void;
  onContinue: () => void;
  account: AccountProfile;
  onSignOut: () => void;
  signingOut: boolean;
}

export default function Connect({ theme, onToggleTheme, onBack, onContinue, account, onSignOut, signingOut, selectedConnectors, onConnectorsChange }: ConnectProps) {
  return (
    <div className="min-h-screen bg-bg text-fg">
      <header className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-6 py-5">
        <Logo />
        <div className="flex items-center gap-2">
          <Button variant="ghost" disabled={signingOut} onClick={onSignOut}>{signingOut ? "Signing out..." : "Sign out"}</Button>
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
        </div>
      </header>

      <main className="mx-auto flex max-w-xl flex-col gap-6 px-6 pt-10 pb-16">
        <div className="flex flex-col gap-2">
          <p className="text-sm text-muted">Signed in as {account.name} · @{account.username}</p>
          <h1 className="text-3xl leading-tight font-semibold tracking-[-0.025em]">Connect your sources</h1>
          <p className="text-[15px] leading-relaxed text-muted">
            Connect Brightspace and Handshake, or browse public EngageSC events.
          </p>
        </div>

        <EngageSCPanel key={account.id} signedIn onSignIn={onBack} />
        <Connectors selected={selectedConnectors} onChange={onConnectorsChange} />
        <CalendarConnection />

        <p className="flex items-center gap-2 text-[13px] text-muted">
          <LockIcon />
          Academic sources are read-only. Google Calendar additions require your approval.
        </p>

        <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
          <Button variant="ghost" onClick={onBack} className="px-0">
            Back
          </Button>
          <Button onClick={onContinue} disabled={signingOut} className="min-h-12 px-6 text-[15px]">
            Continue to chat
          </Button>
        </div>
      </main>
    </div>
  );
}

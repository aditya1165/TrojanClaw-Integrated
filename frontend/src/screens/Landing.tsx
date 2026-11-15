import type { Theme } from "../theme";
import { ArrowRightIcon, Button, LockIcon, Logo, ThemeToggle } from "../components/ui";

interface LandingProps {
  theme: Theme;
  onToggleTheme: () => void;
  onSignIn: () => void;
  onHowItWorks: () => void;
  checkingSession: boolean;
}

export default function Landing({ theme, onToggleTheme, onSignIn, onHowItWorks, checkingSession }: LandingProps) {
  return (
    <div className="min-h-screen bg-bg text-fg">
      <nav className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-6 py-5">
        <Logo />
        <div className="flex items-center gap-1">
          <button type="button" onClick={onHowItWorks} className="min-h-11 px-3 text-sm text-muted hover:text-fg">
            How it works
          </button>
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
        </div>
      </nav>

      <main>
        <section className="flex flex-col items-center gap-6 px-6 pt-20 pb-16 text-center sm:pt-28">
          <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1.5 text-[13px] text-muted">
            <span className="h-1.5 w-1.5 rounded-full bg-gold" />
            Built for USC students
          </span>
          <h1 className="max-w-3xl text-4xl leading-[1.05] font-semibold tracking-[-0.035em] sm:text-6xl">
            Every deadline, planned around your week.
          </h1>
          <p className="max-w-xl text-lg leading-relaxed text-muted">
            Sign in with Google and one USC login, and TrojanClaw connects Brightspace,
            Handshake, WebReg and your calendar. Then just ask.
          </p>
          <div className="mt-2 flex flex-wrap justify-center gap-3">
            <Button onClick={onSignIn} disabled={checkingSession} className="min-h-12 px-5 text-[15px]">
              {checkingSession ? "Checking your session..." : "Get started"}
              <ArrowRightIcon />
            </Button>
          </div>
          <p className="inline-flex items-center gap-1.5 text-[13px] text-muted">
            <LockIcon />
            Academic sources are read-only. Calendar changes always need your approval.
          </p>
          <button type="button" onClick={onHowItWorks} className="min-h-11 text-sm font-medium text-accent-fg hover:underline">
            See how it works
          </button>
        </section>

      </main>
    </div>
  );
}

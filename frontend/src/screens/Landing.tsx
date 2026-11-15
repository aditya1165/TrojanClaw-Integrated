import type { Theme } from "../theme";
import { ArrowRightIcon, Button, LockIcon, Logo, ThemeToggle } from "../components/ui";

const STEPS = [
  {
    title: "Connect once",
    text: "Log in to Brightspace and Piazza in a normal browser window. One login each.",
  },
  {
    title: "Ask anything",
    text: "“What’s due this week?” Every answer links to the Brightspace item or Piazza post it came from.",
  },
  {
    title: "Approve your plan",
    text: "Claude proposes study blocks in your free time. You choose what goes on your calendar.",
  },
];

interface LandingProps {
  theme: Theme;
  onToggleTheme: () => void;
  onContinue: () => void;
  onTrySample: () => void;
}

export default function Landing({ theme, onToggleTheme, onContinue, onTrySample }: LandingProps) {
  return (
    <div className="min-h-screen bg-bg text-fg">
      <nav className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-6 py-5">
        <Logo />
        <div className="flex items-center gap-1">
          <a href="#how" className="px-3 py-3 text-sm text-muted hover:text-fg">
            How it works
          </a>
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
            TrojanClaw reads your Brightspace and Piazza, then suggests study blocks for your Google
            Calendar. Nothing is added until you approve it.
          </p>
          <div className="mt-2 flex flex-wrap justify-center gap-3">
            <Button onClick={onContinue} className="min-h-12 px-5 text-[15px]">
              Continue with Google
              <ArrowRightIcon />
            </Button>
            <Button variant="secondary" onClick={onTrySample} className="min-h-12 px-5 text-[15px]">
              Try with sample data
            </Button>
          </div>
          <p className="inline-flex items-center gap-1.5 text-[13px] text-muted">
            <LockIcon />
            Read-only. TrojanClaw never posts, submits, or sees your USC password.
          </p>
        </section>

        <section id="how" className="mx-auto grid max-w-5xl gap-4 px-6 pb-20 sm:grid-cols-3">
          {STEPS.map((step, index) => (
            <div key={step.title} className="flex flex-col gap-2 rounded-2xl border border-line bg-surface p-6">
              <span className="text-[13px] font-semibold text-accent-fg">0{index + 1}</span>
              <h2 className="text-[17px] font-semibold">{step.title}</h2>
              <p className="text-sm leading-relaxed text-muted">{step.text}</p>
            </div>
          ))}
        </section>
      </main>
    </div>
  );
}

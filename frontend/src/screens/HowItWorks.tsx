import type { ReactNode } from "react";
import type { Theme } from "../theme";
import { Button, Logo, ThemeToggle } from "../components/ui";

interface HowItWorksProps {
  theme: Theme;
  onToggleTheme: () => void;
  signedIn: boolean;
  onBack: () => void;
  onStart: () => void;
}

const SOURCES = [
  { name: "Brightspace", reads: "Courses, assignments, grades, announcements", change: "No" },
  { name: "Handshake", reads: "Jobs, internships, career events", change: "No" },
  { name: "EngageSC", reads: "Public USC campus events", change: "No" },
  { name: "Google Calendar", reads: "Your calendar, to add events", change: "Only events you approve" },
  { name: "Piazza (separate sign-in)", reads: "Class Q&A threads", change: "No" },
];

const QUESTIONS = [
  { q: "Do I sign in every time?", a: "No. TrojanClaw keeps your session. When USC ends it, sign in with USC again and your sources reconnect." },
  { q: "Who else can use my connections?", a: "Nobody. Each TrojanClaw account keeps its own sessions, separate from everyone else's." },
  { q: "Can it submit work or apply to jobs?", a: "No. Sources are read-only. The only thing it can add is a calendar event you approve." },
  { q: "Can I use it without a USC login?", a: "Yes. Create a TrojanClaw username and password, then connect sources from Sources in the app." },
];

function Step({ number, title, children, visual }: { number: number; title: string; children: ReactNode; visual: ReactNode }) {
  return (
    <article className="flex flex-wrap items-center gap-8 rounded-2xl border border-line bg-surface p-6 sm:p-8">
      <div className="flex min-w-0 flex-[1_1_340px] flex-col gap-2.5">
        <span className="text-[13px] font-semibold text-accent-fg">Step {number}</span>
        <h2 className="text-2xl font-semibold tracking-[-0.02em]">{title}</h2>
        <p className="text-base leading-relaxed text-muted">{children}</p>
      </div>
      <div className="flex min-w-0 flex-[1_1_280px] flex-col gap-2.5 rounded-2xl bg-subtle p-5" aria-hidden="true">{visual}</div>
    </article>
  );
}

const Pill = ({ children }: { children: ReactNode }) => (
  <div className="flex items-center gap-2.5 rounded-xl bg-surface px-3.5 py-3 text-sm">{children}</div>
);

export default function HowItWorks({ theme, onToggleTheme, signedIn, onBack, onStart }: HowItWorksProps) {
  return (
    <div className="min-h-screen bg-bg text-fg">
      <nav className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-6 py-5">
        <button type="button" onClick={onBack} aria-label={signedIn ? "Back to chat" : "Back to home"} className="rounded-lg">
          <Logo />
        </button>
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={onBack}>{signedIn ? "Back to chat" : "Back"}</Button>
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
        </div>
      </nav>

      <main>
        <header className="mx-auto flex max-w-3xl flex-col items-center gap-4 px-6 pt-14 pb-12 text-center sm:pt-20">
          <span className="rounded-full border border-line bg-surface px-3 py-1.5 text-[13px] text-muted">How it works</span>
          <h1 className="text-4xl leading-[1.05] font-semibold tracking-[-0.035em] sm:text-5xl">
            One USC login. Your courses, jobs and campus in one chat.
          </h1>
          <p className="max-w-xl text-lg leading-relaxed text-muted">
            TrojanClaw reads the USC sites you already use, so you can ask one question instead of opening five tabs.
          </p>
        </header>

        <section className="mx-auto flex max-w-5xl flex-col gap-5 px-6 pb-20">
          <Step number={1} title="Sign in with USC" visual={<>
            <strong className="text-sm">USC sign-in</strong>
            <Pill><span className="text-muted">NetID and password</span></Pill>
            <div className="rounded-xl bg-accent px-3.5 py-3 text-center text-sm font-semibold text-on-accent">Log in, then approve Duo</div>
          </>}>
            A USC sign-in window opens. Enter your NetID password and approve Duo, the same as on any USC site.
            Your password goes to USC's own page. TrojanClaw does not store it.
          </Step>

          <Step number={2} title="We set things up for you" visual={<>
            <Pill><Check />Brightspace<span className="ml-auto text-xs text-ok">Connected</span></Pill>
            <Pill><Check />Handshake<span className="ml-auto text-xs text-ok">Connected</span></Pill>
            <Pill><span className="flex h-6 w-6 items-center justify-center rounded-full bg-subtle text-muted">+</span>Google Calendar<span className="ml-auto text-xs text-muted">Optional</span></Pill>
          </>}>
            With that same login, TrojanClaw connects Brightspace and Handshake in the background, so you don't sign in
            to each one. Connect Google Calendar too if you want TrojanClaw to add events.
          </Step>

          <Step number={3} title="Ask in plain English" visual={<div className="flex flex-col items-start gap-2.5">
            <Pill>What's due this week?</Pill>
            <Pill>Any software internships closing soon?</Pill>
            <Pill>What's happening on campus Friday night?</Pill>
          </div>}>
            TrojanClaw checks your sources and answers in one place. Use the Sources button in the chat box to choose
            which ones it uses.
          </Step>

          <Step number={4} title="You stay in control" visual={<div className="flex flex-col gap-2 rounded-xl bg-surface p-4">
            <span className="text-xs text-muted">Calendar proposal</span>
            <strong className="text-[15px]">Study for CSCI 270 midterm</strong>
            <span className="text-[13px] text-muted">Thu · 7:00–9:00 PM</span>
            <span className="mt-1 rounded-lg bg-accent py-2 text-center text-[13px] font-semibold text-on-accent">Approve addition</span>
          </div>}>
            Your sources are read-only: TrojanClaw never posts, submits or applies for you. It can draft a calendar event,
            but nothing is added until you press Approve.
          </Step>
        </section>

        <section className="mx-auto flex max-w-5xl flex-col gap-4 px-6 pb-20">
          <h2 className="text-[28px] font-semibold tracking-[-0.02em]">What TrojanClaw can see</h2>
          <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
            <table className="w-full min-w-[560px] border-collapse text-left text-sm">
              <thead>
                <tr className="text-[13px] text-muted">
                  <th className="px-5 py-3.5 font-semibold">Source</th>
                  <th className="px-5 py-3.5 font-semibold">What it reads</th>
                  <th className="px-5 py-3.5 font-semibold">Can it change anything?</th>
                </tr>
              </thead>
              <tbody>
                {SOURCES.map((row) => (
                  <tr key={row.name} className="border-t border-line">
                    <td className="px-5 py-3.5 font-semibold">{row.name}</td>
                    <td className="px-5 py-3.5 text-muted">{row.reads}</td>
                    <td className="px-5 py-3.5">{row.change}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="mx-auto flex max-w-5xl flex-col gap-4 px-6 pb-20">
          <h2 className="text-[28px] font-semibold tracking-[-0.02em]">Questions</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {QUESTIONS.map((item) => (
              <div key={item.q} className="flex flex-col gap-2 rounded-2xl border border-line bg-surface p-6">
                <h3 className="text-base font-semibold">{item.q}</h3>
                <p className="text-sm leading-relaxed text-muted">{item.a}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mx-auto max-w-5xl px-6 pb-24">
          <div className="flex flex-col items-center gap-4 rounded-3xl bg-accent px-8 py-12 text-center text-on-accent">
            <h2 className="text-3xl font-semibold tracking-[-0.02em]">{signedIn ? "You're all set" : "Ready when you are"}</h2>
            <p className="opacity-85">{signedIn ? "Your sources are a click away in the chat box." : "It takes about a minute, including Duo."}</p>
            <button type="button" onClick={onStart}
              className="min-h-12 rounded-xl bg-gold px-6 text-[15px] font-semibold text-[#1c1917] hover:brightness-105">
              {signedIn ? "Open chat" : "Sign in with USC"}
            </button>
          </div>
        </section>
      </main>
    </div>
  );
}

function Check() {
  return (
    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-ok-soft text-ok">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.8"
        strokeLinecap="round" strokeLinejoin="round"><path d="M5 12l5 5 9-10" /></svg>
    </span>
  );
}

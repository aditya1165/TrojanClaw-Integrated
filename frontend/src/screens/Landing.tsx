import type { Theme } from "../theme";
import { ArrowRightIcon, Button, LockIcon, Logo, ThemeToggle } from "../components/ui";
import { BrandMark } from "../components/CampusWelcome";

interface LandingProps {
  theme: Theme;
  onToggleTheme: () => void;
  onSignIn: () => void;
  onHowItWorks: () => void;
  checkingSession: boolean;
}

export default function Landing({ theme, onToggleTheme, onSignIn, onHowItWorks, checkingSession }: LandingProps) {
  return (
    <div className="landing-shell min-h-screen bg-bg text-fg">
      <nav className="landing-nav mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-5">
        <Logo />
        <div className="flex items-center gap-1">
          <button type="button" onClick={onHowItWorks} className="min-h-11 px-3 text-sm text-muted hover:text-fg">
            How it works
          </button>
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
        </div>
      </nav>

      <main className="mx-auto max-w-6xl px-6">
        <section className="landing-hero">
          <div className="landing-copy">
            <p className="landing-eyebrow"><span /> Built for the Trojan everyday</p>
            <h1>Your campus.<br /><em>All together.</em></h1>
            <p className="landing-description">From the next deadline to your next big thing. Bring your USC day into one calmer conversation.</p>
            <div className="landing-actions">
              <Button onClick={onSignIn} disabled={checkingSession} className="landing-cta min-h-12 px-6 text-[15px]">
                {checkingSession ? "Checking your session…" : "Find your rhythm"}<ArrowRightIcon />
              </Button>
              <button type="button" onClick={onHowItWorks} className="min-h-11 text-sm font-medium text-muted hover:text-fg">Take a look around <span aria-hidden="true">↗</span></button>
            </div>
            <p className="landing-trust"><LockIcon /> Your calendar only changes with your approval.</p>
          </div>
          <div className="campus-poster" aria-label="A preview of your connected workspace">
            <div className="poster-topline"><span>THE BIG PICTURE</span><span aria-hidden="true">✳</span></div>
            <BrandMark className="poster-mark" />
            <p className="poster-title">A few less tabs.<br /><em>A little more headspace.</em></p>
            <div className="poster-sources"><span><i className="source-brightspace">B</i>Brightspace</span><span><i className="source-handshake">H</i>Handshake</span><span><i className="source-calendar">G</i>Calendar</span><span><i className="source-piazza">P</i>Piazza</span></div>
            <button type="button" className="poster-question" onClick={onSignIn} disabled={checkingSession}><span>“What does my week look like?”</span><ArrowRightIcon /></button>
            <p className="poster-caption">One question. A more connected day.</p>
          </div>
        </section>
        <section className="landing-footnotes" aria-label="What you can do">
          <div><span className="footnote-number">01 /</span><p><strong>Ahead of the deadline.</strong><span>Bring your course updates into focus.</span></p></div>
          <div><span className="footnote-number">02 /</span><p><strong>Beyond the classroom.</strong><span>Find campus events and opportunities.</span></p></div>
          <div><span className="footnote-number">03 /</span><p><strong>A week that works.</strong><span>Make plans, with you in control.</span></p></div>
        </section>
      </main>
      <footer className="landing-footer mx-auto max-w-6xl px-6"><span>TrojanClaw · Your campus companion</span><span>Made for a little more possibility.</span></footer>
    </div>
  );
}

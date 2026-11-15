import { ArrowRightIcon, CalendarIcon, FileIcon, SunIcon } from "./ui";

export function BrandMark({ className = "" }: { className?: string }) {
  return <div className={`brand-mark ${className}`} aria-hidden="true">
    <img src="/logo.gif" alt="" />
  </div>;
}

const starters = [
  { title: "Get a little ahead", description: "See what’s due this week", prompt: "What deadlines and assignments do I have coming up this week?", icon: <FileIcon />, color: "sage" },
  { title: "Make some room", description: "Find a moment to focus", prompt: "Help me find time for a study session this week. Check my calendar first.", icon: <CalendarIcon />, color: "rose" },
  { title: "Beyond the classroom", description: "Discover something on campus", prompt: "What interesting events are happening on campus this week?", icon: <SunIcon />, color: "gold" },
  { title: "See the bigger picture", description: "Take a look at your calendar", prompt: "What does my calendar look like for the next seven days?", icon: <CalendarIcon />, color: "blue" },
];

export default function CampusWelcome({ firstName, timezone, onPrompt, disabled }: { firstName: string; timezone: string; onPrompt: (prompt: string) => void; disabled: boolean }) {
  const hour = Number(new Intl.DateTimeFormat("en-US", { timeZone: timezone, hour: "numeric", hourCycle: "h23" }).format(new Date()));
  const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  return <section className="campus-welcome" aria-label="Welcome to your campus workspace">
    <BrandMark />
    <h1>{firstName ? <>{greeting},{" "}<span className="welcome-name">{firstName}.</span></> : "Welcome back."}</h1>
    <p className="welcome-description">What’s on your mind today?</p>
    <div className="starter-grid">
      {starters.map(starter => <button key={starter.title} type="button" title={starter.description} className={`starter-card starter-${starter.color}`} disabled={disabled} onClick={() => onPrompt(starter.prompt)}>
        <span className="starter-icon">{starter.icon}</span>
        <span className="starter-copy"><span className="starter-title">{starter.title}</span></span>
        <span className="starter-arrow"><ArrowRightIcon /></span>
      </button>)}
    </div>
  </section>;
}

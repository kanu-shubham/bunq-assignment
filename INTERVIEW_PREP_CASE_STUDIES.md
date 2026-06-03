# Two Case Studies for Tomorrow's Interview
## Agentic Interface Lead, Applied AI — JPMorganChase

> Two STAR-format stories from your real experience, framed for this role.
> Memorise the structure, not the words. Each should take 4–6 minutes to tell.

---

# CASE STUDY 1 — The Microsoft 11-Agent System
### (Your strongest agentic + HITL story — lead with this)

**Use this when asked:** "Tell me about your most impactful project" / "Describe an agentic system you built" / "How do you handle the trust problem with AI?"

### S — Situation
> "At Microsoft I was on the Power Pages platform — 400M monthly active users. Internally, building a new mobile app from a design still took an engineer three to four days of scaffolding: setting up navigation, state management, components, accessibility, tests. It was repetitive, error-prone work that didn't use anyone's creativity. Leadership wanted to know if AI could compress that."

### T — Task
> "I was asked to architect a system that could take a single natural-language prompt or a design spec and produce a production-ready React Native application. The hard constraint was that the output had to be *trusted* — engineers wouldn't adopt a black box that spat out code they couldn't verify. So it wasn't just 'make AI generate code'; it was 'make AI generate code engineers will actually ship.'"

### A — Action
> "I made three key architectural decisions.
>
> **First, I decomposed the problem into eleven specialised agents** rather than one monolithic prompt — a component agent, a state-management agent, a navigation agent, an accessibility agent, a test-generation agent, and so on. Each owned a narrow concern, which made each independently testable and dramatically improved output quality versus one giant prompt trying to do everything.
>
> **Second, I built a RAG layer** over our component library and design-system documentation so agents generated code that matched our actual conventions — not generic React Native, but *our* patterns, *our* components.
>
> **Third — and this is the part I'm proudest of — I designed the HITL interface.** Instead of dumping a finished app, the system exposed its reasoning at each step: here's the plan, here's what the component agent decided and why, here's the accessibility check. The engineer could inspect, interrupt, correct any step, and re-run from that checkpoint. The AI proposed; the engineer stayed in control."

### R — Result
> "We cut the scaffolding cycle by 65% — three-to-four days down to four-to-six hours including human review. But the metric I care about more is adoption: engineers leaned into it instead of bypassing it, precisely because they could see and steer what it was doing. That taught me the central lesson I'd bring to this role — the model quality matters, but the *interface that lets a human trust and direct the model* is what determines whether an agentic system actually gets used."

### The bridge to JPM (always end here)
> "That exact pattern — decompose into specialised agents, ground them with RAG, and put a human in control through a transparent interface — is what operational workflows at JPM need. A settlement break or a margin-call dispute is the same shape of problem, except the blast radius is real money, so the HITL layer matters even more."

### Likely follow-ups & your answers
- **"What did you do when an agent produced wrong output?"** → "The accessibility agent occasionally over-flagged. Two things: I added a deterministic validation pass *after* the agent — the agent proposes, a linter decides — and I surfaced the disagreement to the engineer rather than hiding it. Same propose/validate/human-confirm split I'd use in finance."
- **"Why 11 agents and not one?"** → "Separation of concerns. One prompt doing everything has no testable boundaries and degrades unpredictably. Narrow agents have clear contracts, can be evaluated independently, and you can upgrade one without destabilising the rest."
- **"How did you orchestrate them?"** → "A graph-based orchestrator routing intent through the right sequence, passing structured context between agents — conceptually what LangGraph gives you, with explicit state at each node."

---

# CASE STUDY 2 — The Morgan Stanley FX Trading Platform
### (Your finance-domain + real-time-correctness story)

**Use this when asked:** "Tell me about your finance experience" / "Describe a high-stakes real-time UI" / "Tell me about a hard technical problem."

### S — Situation
> "At Morgan Stanley I worked on the FX trading platform — the UI traders used for order management and options trading, with live pricing streaming over WebSocket. In FX, prices move many times a second, and a trader makes irrevocable decisions on what's on screen. If the displayed price is stale or wrong by even a moment, that's not a UX bug — it's a real financial loss and a potential dispute."

### T — Task
> "I owned the pricing and order-management UI. The challenge was twofold: keep a high-frequency price stream rendering smoothly without janking the rest of the interface, and guarantee that what the trader saw was *correct* — never a stale price presented as live, never an order action firing against a price that had already moved."

### A — Action
> "Several decisions.
>
> **On performance:** the naive approach re-renders the whole blotter on every tick. I isolated each price cell into its own subscription so a tick updated only that cell, not the table — and batched updates to animation frames so we never rendered faster than the screen refreshed. The rest of the UI — filters, order forms — stayed responsive because price updates couldn't block them.
>
> **On correctness:** I treated staleness as a first-class state. Every price carried a timestamp; if a feed gap exceeded a threshold, the cell visibly flipped to a 'stale' state and order actions against it were disabled until a fresh tick arrived. We never let the UI *imply* a price was tradeable when it might not be.
>
> **On the order lifecycle:** I built the UI around explicit order states — pending, working, filled, rejected — driven by the server, not optimistic guesses, because in trading an optimistic 'filled' that turns out false is worse than a half-second delay.
>
> I also built the cross-platform component library and design system so web, mobile, and desktop shared one consistent, tested FX component set."

### R — Result
> "Trade processing speed improved 28%, user satisfaction 23%, and the component library cut time-to-market for new FX features by 50%. More importantly, the staleness-as-a-state design eliminated a class of 'I traded on a bad price' complaints. That experience is why I think about AI in finance the way I do: correctness and honest representation of uncertainty beat raw speed or cleverness."

### The bridge to JPM
> "This is why the 'LLMs propose, humans commit' model resonates with me — I've lived the consequence of a UI presenting something as more certain than it was. In an agentic operations platform, the equivalent discipline is: never let the interface imply the AI's proposal is a decision. Show it as a proposal, show the confidence, show the rule that validated it, and keep the human in the commit path."

### Likely follow-ups & your answers
- **"How did you handle the high-frequency re-render problem specifically?"** → "Per-cell subscriptions plus requestAnimationFrame batching, and `memo` so unchanged cells skipped render entirely. I profiled with the DevTools flamegraph to confirm only changed cells re-rendered."
- **"WebSocket here but you'd pick SSE for the workbench — why the difference?"** → "FX pricing is genuinely bidirectional and ultra-low-latency — WebSocket is right. The ops workbench is server→client state push with human commands over REST; SSE is simpler, survives proxies, auto-reconnects, and doesn't need sticky sessions. Pick the transport for the actual interaction pattern, not by default."
- **"What would you do differently today?"** → "I'd formalise the price-state machine with something like XState — staleness, gaps, and recovery as explicit guarded transitions rather than imperative flags. Cleaner and more testable."

---

## HOW TO DEPLOY THESE TWO

| If they ask… | Lead with |
|---|---|
| Most impactful / agentic / AI trust | **Case Study 1** (Microsoft) |
| Finance domain / real-time / high-stakes UI | **Case Study 2** (Morgan Stanley) |
| A hard technical problem | Either — pick by interviewer's interest |
| A failure / what went wrong | The accessibility-agent over-flagging (CS1) or a price-feed gap incident (CS2) |

## THE TWO SENTENCES THAT TIE THEM TOGETHER
> "Microsoft taught me how to build agentic systems that humans actually trust and steer. Morgan Stanley taught me what's at stake when a financial UI gets correctness wrong. This role sits exactly at the intersection of those two — and that intersection is where I want to do my best work."

## PREP CHECKLIST FOR TOMORROW
- [ ] Tell each story out loud, timed to under 6 minutes
- [ ] Have the numbers ready: 65%, 11 agents, 400M MAU, 28%, 23%, 50%, 1,800 stores
- [ ] For each, know the ONE hard decision and the ONE thing you'd do differently
- [ ] Practice the bridge sentence — that's what makes it land for *this* role
- [ ] Don't recite — converse. Let them interrupt; that means they're engaged.

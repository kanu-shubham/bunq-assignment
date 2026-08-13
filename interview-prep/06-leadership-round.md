# 06 — The leadership round

The posting describes a **hands-on** lead: coaching engineers, owning hiring and performance,
setting technical direction, and contributing to the codebase when needed. It also stresses
working across Solutions Engineering, Implementation Success and Business Development.

That combination tells you what they're screening for: someone who can hold technical
authority *and* translate. Prepare accordingly.

---

## Prepare six stories

Six well-prepared stories cover almost every behavioural question. Write them out — the act of
writing fixes the details, and vagueness is what sinks these answers.

Use **STAR**: Situation, Task, Action, Result. Keep Situation to two sentences; spend your time
on Action and Result.

| # | Story | Probes |
|---|---|---|
| 1 | **Grew someone.** A specific engineer, what they couldn't do, what you did, where they got to. | Mentoring, "growing senior ICs" |
| 2 | **A hard technical call.** Options, trade-offs, decision, outcome — including what it cost. | Architecture, judgement |
| 3 | **Delivered under ambiguity.** Unclear requirements, moving target, shipped anyway. | "Bias to action and comfort with ambiguity" |
| 4 | **Disagreed with a stakeholder.** Ideally product or commercial. How you resolved it. | Cross-functional, the intersection this team sits at |
| 5 | **Something you got wrong.** Real, owned, with the specific change you made after. | Self-awareness. **The most common failure point.** |
| 6 | **Raised the bar.** A practice you introduced — testing, review, CD — and its measured effect. | "Engineering excellence" |

### Numbers

Every Result needs one. Not "it was much faster" but "p99 went from 1.2s to 240ms" or "onboarding
a new partner went from six weeks to nine days". If you don't have exact figures, an honest
estimate framed as one is fine: *"roughly a third fewer support tickets — I don't have the exact
number to hand."*

### On story 5

The common failure is picking a fake weakness ("I care too much about quality") or a story
where you were secretly right. Pick something that actually cost the team, own your part
without theatrical self-flagellation, and be concrete about what you changed. Interviewers at
this level are calibrated for this and a dodge is very visible.

---

## Questions they're likely to ask

### "How do you grow a senior engineer?"

The trap is describing how you'd grow a *junior* — more structure, more review. Seniors need
the opposite.

> *"Differently from a mid-level engineer. Mid-level growth is mostly technical breadth. Senior
> growth is scope: influence beyond their own work, taking on the ambiguous problem nobody has
> framed yet, making other people better. So I give context and outcomes rather than tasks —
> 'partner onboarding takes six weeks and it needs to take one, you own it' rather than a
> ticket list. Then I get out of the way, stay available, and make sure the work is visible to
> the people whose opinion affects their progression. The main mistake I've made here is
> staying involved too long because the problem was interesting to me."*

### "How do you make architectural decisions in a team?"

> *"I write them down. Lightweight ADRs — context, options, decision, consequences. Most of the
> value isn't the record, it's that writing forces you to articulate the trade-off, and half
> the time the act of writing changes my mind. For reversible decisions I push the call to
> whoever's doing the work; for one-way doors — data models, public API contracts, anything a
> partner integrates against — I want the team's disagreement surfaced before we commit, and I
> take the decision if we don't converge. I'd rather be argued out of something early than
> discover the objection in production."*

The one-way-door framing is worth having; it's a genuinely useful distinction and it lands well.

### "How do you stay hands-on without becoming the bottleneck?"

Directly relevant — the posting asks for exactly this.

> *"I stay in the code, but off the critical path. Code review is most of it — it's the highest-
> leverage way to stay current and to teach at the same time. Beyond that I take the unglamorous
> work: test infrastructure, the flaky pipeline, the observability gap, the tricky migration. It
> keeps me honest about what the codebase is actually like to work in, and it doesn't block
> anyone if I get pulled into hiring for a week. What I try not to do is take the interesting
> feature — that's the growth opportunity someone else needs, and it makes me a dependency."*

### "How do you explain technical constraints to non-technical stakeholders?"

This team sits between Solutions Engineering and Business Development, so expect it.

> *"In their units, not mine. 'We can't hold the connection open' means nothing to a commercial
> lead; 'if we do it that way, one slow partner takes down payments for every other partner'
> lands immediately. I try to give options with costs rather than a no — 'we can ship the
> simple version for this partner in two weeks, but it won't scale past about ten of them, so
> we'd redo it next year; or four weeks and it's the pattern for everyone.' That's a business
> decision and they're better placed to make it than I am. What I won't do is present a
> technical constraint as immovable when it's really a cost."*

### "Tell me about a time you disagreed with your product manager."

Show partnership, not victory. The good answer ends with a shared decision and a mechanism —
a spike, a metric, an agreed checkpoint — rather than someone conceding.

### "How would you approach your first 90 days?"

> *"First month, mostly listening. Read the code and the incidents — incident history tells you
> more about a system than any document. Talk to every engineer on the team, and to Solutions
> Engineering and the partners' implementation people, because they see the pain we've shipped.
> Ship something small and real in week two or three, mostly to learn the delivery pipeline
> from the inside.*
>
> *Second month, form a view and test it. By then I should be able to say where the friction
> is — onboarding time, on-call load, a component everyone's afraid of — and check that against
> what the team thinks.*
>
> *Third month, commit to a direction with the PM and start moving. I'd want one visible
> improvement landed by then, chosen more for demonstrating we can change things than for its
> own size."*

---

## Wise-specific preparation

Do this properly; it takes an hour and it shows.

- **Read the [Wise engineering blog](https://medium.com/wise-engineering)**, particularly the
  2025 tech stack update the posting links. Reference something specific from it. Nothing
  signals genuine interest like *"I read your piece on X and wondered about Y"*.
- **Understand the product**: Wise holds local float in many countries and does local payouts on
  both sides, netting the difference, rather than pushing money through correspondent banking
  chains. That's the source of the speed and price advantage — and it's exactly what makes
  "Send for Partners" attractive to a bank that doesn't want to build it.
- **Their mission** — "money without borders", minimum fees, maximum ease — is used internally
  as a decision-making tool, not just marketing. Framing a technical answer in terms of customer
  cost or speed is speaking their language.
- **Their autonomy model.** Wise talks a lot about autonomous teams and "leadership through
  empowerment by providing context, trust and clear direction" (their words, in this posting).
  Have a real view on how you give context rather than instructions, and an example.

### Questions to ask them

Ask things you actually want to know. These are good because the answers genuinely change what
the job is:

- "What does the team own end to end, and where does it hand off to other Wise Platform teams?"
- "What's the biggest source of toil for the team right now — is it partner onboarding, on-call,
  something else?"
- "How long does it take to onboard a new partner today, and where does that time go?"
- "How does the team decide between partner-specific work and platform investment? Who arbitrates
  when a big partner wants something bespoke?"
- "What does on-call look like, given payments don't stop at 6pm?"
- "What would make you say, a year from now, that this hire went really well?"

That last one is worth asking in every leadership interview. The answer tells you what you'd
actually be measured on, and it's often different from the job description.

---

## Two failure modes to avoid

**Over-indexing on the technical.** You have prepared a lot of distributed-systems material and
it will be tempting to steer every answer there. Half of this role is people. An answer to "how
do you grow engineers" that becomes a discussion of architecture is a miss.

**Underselling because of the Java gap.** You are strong at what actually matters here. Being
new to Java is a fact to state plainly once, with evidence that you close gaps fast — the
service in this repo *is* that evidence — and then move on. Do not apologise for it twice.

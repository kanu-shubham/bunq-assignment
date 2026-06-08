# Hiring Manager Screen — Einar Magnusson, Applied AI ML Director, JPMorganChase
## 30 minutes. Every second counts.

---

## WHO IS EINAR

- **Title:** Applied AI ML Director — he builds production AI systems, not research prototypes.
- **What he's hiring for:** Agentic Interface Lead — someone who owns the human↔agent boundary end-to-end.
- **What he fears hiring:** Someone who can build React but doesn't understand agentic system design. Or someone who understands agents but can't ship production UI.
- **The single question he's answering in this call:** "Does this person think in systems, not features — and have they shipped the hard version of this problem?"

---

## THE 30-MINUTE MAP

```
00:00–02:00   He sets context on the role
02:00–05:00   "Tell me about yourself" → YOUR OPENING PITCH
05:00–15:00   Discussion of your recent projects (he drives)
15:00–22:00   Your perspectives on AI and its future
22:00–28:00   Interface design and agentic workflows
28:00–30:00   Your questions to him
```

---

## BLOCK 1 — OPENING PITCH (90 seconds, no more)

Never recite your CV. Deliver a narrative with a thesis:

> "I'm a senior engineer with ten years in frontend, the last few of which I've spent at the boundary between AI systems and the humans who have to trust them — which is exactly what this role is.
>
> At Morgan Stanley I built FX trading UIs — real-time pricing, order management, options — so I understand the domain constraint: when a number is wrong, someone loses money. That shaped how I think about AI in finance.
>
> At Microsoft I designed the Copilot integration for Power Pages and then built a multi-agent system — eleven specialized agents orchestrated together — that generates production React Native apps from a single prompt, cutting development cycles by 65%. The hardest part wasn't the models. It was designing the interface so operators could trust what the agents did and intervene when they needed to.
>
> That pattern — agents proposing, humans committing, with full auditability — is the pattern I'd bring to JPM's operational workflows. And honestly, settlements and reconciliations and margin-call disputes are the most interesting version of that problem, because the blast radius is real."

**Why this works:**
- Finance background → domain credibility before he asks
- Microsoft 11-agent system → proves you've built real agentic orchestration
- "Blast radius" → shows you understand why this role is different from consumer AI
- Ends on enthusiasm for *his specific domain*

---

## BLOCK 2 — RECENT PROJECTS

He will ask: *"Walk me through your most impactful recent project."*
Then: *"Tell me about a time the AI got it wrong and how you handled it."*
Then: *"How did you handle the trust problem?"*

### Project A — Microsoft Multi-Agent System (lead with this)

**The what:**
> "I architected a system with eleven specialized AI agents — each owns a specific concern: component architecture, state management, accessibility validation, styling, test generation, and so on. The orchestrator routes intent through the right agent sequence, passes structured context between them, and synthesizes a production-ready React Native application from a single prompt."

**The hard part (what separates you from CRUD developers):**
> "The hard part wasn't wiring up LangGraph or calling the API. It was: how does the user know which agent made which decision, and why? How do they intervene when the architecture agent proposes something wrong? I designed the UI to expose agent reasoning at each step — a plan the user can inspect, interrupt, correct, and re-run from any checkpoint. That's the HITL layer — and it's the part most people skip because it's harder than the model work."

**The result:**
> "65% reduction in development cycles across the team. But the metric I'm prouder of is that operators trusted it — they didn't bypass the AI, they leaned into it, because they could see what it was doing and why."

**The finance bridge:**
> "The exact same architecture — proposal, validation, human commit, audit — is what I'd apply to a settlement break or a margin-call dispute. The stakes are higher, the blast radius is real money, but the trust problem is identical."

---

### Project B — Morgan Stanley FX Platform (the domain card)

Play this when he probes your finance experience:

> "At Morgan Stanley I built the FX trading platform — WebSocket-driven real-time pricing, order management, options trading UIs. What that taught me is that in finance, UI correctness is not about pixels — it's about presenting data a human will make an irrevocable decision on. A stale price, a wrong sign, a missing flag — those are not UX bugs. They're risk events. That experience is why I think about AI in finance very differently from AI in consumer products."

---

### Project C — Copilot at Microsoft (the trust problem in production)

> "I led the Copilot integration for Power Pages. The first version showed AI suggestions — users ignored them. Support tickets didn't move. We redesigned it: every suggestion shows its source, its confidence, and a clear accept/reject. Support tickets dropped 45%. The lesson was: the interface design matters as much as the model. Users don't distrust AI because it's wrong — they distrust it because they can't verify it. Once you give them the verification layer, they engage."

---

## BLOCK 3 — YOUR PERSPECTIVES ON AI AND ITS FUTURE

This is the section most candidates treat as opinion. Einar is testing **intellectual depth and original thinking**, not whether you agree with the consensus.

### On the state of AI today

> "We're in the phase where the models are ahead of the interfaces. Foundation models can do extraordinary things, but most of the value is locked up because the human↔agent interaction layer hasn't been designed properly. The problem isn't capability — it's trust calibration. Humans either overtrust AI output at scale, which is more dangerous than not using AI at all, or they undertrust it and revert to manual processes. The design challenge is building interfaces that put the human in exactly the right level of control for the stakes involved."

### On the future of AI in operations

> "I think the next three to five years in operational AI aren't about automating more — they're about making human decisions faster and better-informed. The win isn't 'the AI settled the trade.' The win is 'a human settled the trade in two minutes instead of two hours, because the AI surfaced the right information, flagged the right risk, and drafted the right response — and the human signed their name to it.' Accountability stays with the human. The AI is a very powerful analyst."

### On the agent architecture you believe in

> "The pattern I keep coming back to: LLMs propose, deterministic rules decide, humans commit, ledgers remember. The LLM is the lowest-trust component — not because it's bad, but because it's probabilistic and you cannot audit a probability in front of a regulator. The rules engine is deterministic and auditable. The human is accountable. The ledger is the proof. If you maintain those distinctions architecturally — not as a convention, but in the code — you get a system that's both useful and safe."

### On where most teams get it wrong

> "Most teams either over-constrain the LLM — it's so hedged it adds no value — or they let it call commit tools directly with an 'approval required' flag as a UX gesture. That second pattern is dangerous. The approval flow has to be a workflow state, not an annotation on a tool call. It has to survive process restarts, support N-of-M signers, and produce an auditable receipt. The chat interface is the wrong mental model for this domain — you need a durable workflow with HITL as a first-class state, not a chat thread with a yes/no button."

---

## BLOCK 4 — INTERFACE DESIGN AND AGENTIC WORKFLOWS

He will ask: *"How do you think about designing interfaces for agentic workflows?"*
And possibly: *"What frameworks have you used? What did you learn?"*

### The design philosophy answer

> "The first principle I start from is: what decision is this human actually making, and what do they need to make it well in under ten seconds? In an operational workflow, that's usually: what changed, why the agent recommends it, what rule flagged it, who else has signed off, and what happens if I'm wrong. If the interface answers those five things clearly, the human can approve with confidence. If it doesn't, they either rubber-stamp blindly — which defeats the point — or they escalate everything, which creates a bottleneck.
>
> The second principle: the interface must reflect the state machine, not the chat thread. Operators need to see that a workflow is in AWAITING_APPROVAL, not scroll through a conversation to figure out where things stand. The workbench pattern — task queue sorted by risk and SLA, drill-in to diff + evidence + approval gate — is right for this domain. Chat is one input affordance within that workbench, not the product."

### The framework answer (CopilotKit / AG-UI / Vercel AI SDK)

> "I've worked with AG-UI as an event protocol — it's the right abstraction because it decouples the server-side agent graph from the client rendering. Standard events handle the common patterns; you extend with typed domain-specific events for things like APPROVAL_REQUEST or EVIDENCE_PINNED. The client subscribes to the event stream and updates the workbench state reactively.
>
> CopilotKit is good for the chat affordance within a larger workbench — it handles the streaming message rendering and tool-call display well. Vercel AI SDK is cleaner for pure streaming UIs but less opinionated about HITL patterns.
>
> The important thing I've learned is: pick the event protocol first, then pick the rendering library. The event shape is what flows from the Python/LangGraph backend to the React frontend — get that contract right and the UI layer becomes straightforward."

### The LangGraph UI answer

> "LangGraph is where I've spent the most time on the agent graph side. The thing it gets right is making state explicit — the graph nodes are the state machine, the edges are the transitions, and with a Postgres or SQLite checkpointer the whole thing is durable and replayable. The UI integration challenge is that LangGraph doesn't prescribe how you expose state to the frontend — you have to build the SSE layer yourself and decide what events to emit at which nodes. I've learned to emit at every significant state transition, not just final outputs, so the UI can show progress and the operator can interrupt early if something looks wrong."

---

## BLOCK 5 — YOUR QUESTIONS (ask 2–3, pick from these)

Never ask about salary, location, benefits, or working hours in the HM screen. Ask about the work.

**Question 1 — shows domain depth:**
> "What does the current HITL surface look like for settlements and reconciliation workflows today — is there a purpose-built workbench, or are operators still working primarily from email and spreadsheets?"

**Question 2 — shows architectural depth:**
> "How does your team think about the boundary between the LLM advisory layer and the deterministic rules engine — is that separation codified in the architecture today, or is it still being worked out?"

**Question 3 — shows you've read the org:**
> "Ash Booth runs AI for Markets Operations — is the interface lead role closer to platform/infrastructure, or is it embedded in specific workflow teams like settlements or margin call disputes?"

**Question 4 — shows long-term thinking:**
> "As you expand the agentic platform to more workflow types, what's the hardest trust problem you're anticipating — is it the operator trusting the AI output, the regulator accepting the audit trail, or something else?"

---

## ANTI-PATTERNS — WHAT NOT TO DO

| Don't | Why |
|---|---|
| Lead with "400M MAU" | He cares about architecture depth, not scale metrics |
| Say "I used LangChain" without context | Say what you built and what problem it solved |
| Agree with everything he says | He's an ML Director — he'll probe to find your actual views |
| Over-claim the HITL Finance Stack as deployed production | It's a design artefact — frame it as your architectural thinking |
| Ask no questions | Signals you're not genuinely engaged with the problem |
| Run over 90 seconds on any single answer | He has 30 minutes and three topics to cover |

---

## THE SINGLE MOST MEMORABLE THING YOU CAN SAY

If you get one moment to land something that sticks:

> "The systems I trust are the ones where the LLM is the *least* trusted component — not because it's bad, but because it's probabilistic, and accountability in finance requires determinism. You design the architecture so that the LLM can be wrong without consequences, and the human is always in the commit path."

Einar will remember that sentence. It shows you think in systems, you understand the domain constraint, and you've arrived at a non-obvious conclusion from first principles.

---

## THE NIGHT BEFORE — WHAT TO REVIEW

1. **Your three projects** — practice saying each in 90 seconds, with the hard problem and the result
2. **LangGraph state machines and checkpointers** — be fluent on this
3. **AG-UI event protocol** — know the standard events and why you'd extend them
4. **The settlement / reconciliation / margin-call lifecycle** — know T+1, UTI, EMIR basics at the conceptual level
5. **This file** — the opening pitch should be rehearsed out loud, not just read

---

## LOGISTICS

- **Format:** Zoom, 30 minutes
- **Your setup:** quiet room, camera on, second monitor with this doc open — glance only, never read
- **Pace:** he will interrupt — that's good, it means he's engaged
- **If you don't know something:** "I haven't worked with that specifically, but here's how I'd think about it..." — never bluff an ML Director

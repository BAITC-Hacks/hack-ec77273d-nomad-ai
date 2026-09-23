# Requirements review

Reviewed the supplied five-page HackAlem AI technical specification, Career Quest case (pages 2–5), alongside the original build brief and starter-kit README. Voice Router is a separate case and is outside this project. Gamification and localization are optional; neither is necessary for the required workflow.

| Requirement | Result |
| --- | --- |
| Arbitrary employee profile, skills, completed activities, trajectory | Implemented; no-goal employees now target the next grade (Lead remains Lead). |
| 1–3 relevant next steps | Deterministic multi-factor ranking; zero is returned honestly when no eligible event closes a target gap. Such employees appear in HR. |
| Explanation based on at least three factors | Every response includes deterministic target-grade, skill-gap/gain, and participation-history evidence, including neutral history. LLM wording cannot hide that evidence. |
| Accurate skill progression | Post-assessment completions are applied at dataset load, exactly once, with caps. New demo completions update skills and rerank. Existing skills above an event cap never decrease. |
| HR skill gaps, participation, no next step | Implemented; no-benefit activities no longer hide employees with no useful recommendation. |
| Judge profiles and history | Atomic replacement retained; merge mode added for additional profiles referencing existing managers. |
| UI response within 2 seconds | Profile fetch/render no longer waits for LLM recommendations. Actual transport and machine performance still affect latency. |
| AI recommendation within 10 seconds | Three explanation calls run concurrently with a shared 7-second response deadline and immediate template fallback on expiration; no retries. |
| Launch with one command | `python run.py` creates the virtual environment if needed, installs approved dependencies, and runs the app. First-run installation requires network. |
| README and repository | README updated; local Git repository present. No remote publication or external sharing is performed. |
| No employee leaderboard, no points for mandatory work | No employee ranking or compulsory-process reward system is implemented; mandatory activities excluded from recommendations. |

## Correctness fixes

- Historical gains after assessment were previously ignored.
- No-goal employees were compared with their existing grade rather than the next grade.
- Templates often omitted target grade and history, failing the three-factor requirement.
- Sequential explanation calls could wait for three independent client timeouts.
- The UI waited for explanations before showing the profile.
- Events with zero achievable target benefit could fill recommendation slots.
- Self-paced assignment dates were incorrectly treated as evidence of on-time completion.
- A same-day completed recurring activity could be recommended again even though completion was idempotent.
- The judge loader only supported full replacement, making small additional profile sets awkward.

## Validation and honest limits

The automated suite covers the original adversarial Public Speaking/System Design case, real-data eligibility, missing skills, caps, next-grade fallback, historical replay, three-factor explanations, a slow-LLM deadline, API completion, stale versions, HR gating, atomic replacement and judge-profile merge. Live model behavior requires an API key and remains unverified; the no-key path is the stage-safe default.

The earlier user brief explicitly requested a simple `?role=hr` gate and no authentication system. That remains a **demo-only exception** to the PDF's production privacy/access-control requirement. Any local user can switch profiles or supply the HR parameter. This is not secure identity isolation and must not be deployed as a real employee portal. No real personal data is included.

The CSV has no actual completion timestamp. Post-review replay uses its provided date as instructed by the starter-kit chronology; same-day assessment records are considered already included. On-time boosts require dated scheduled-event evidence; no timing claim is made for self-paced assignments or records without deadlines.

All state remains in one process and resets on restart. No persistence, production authentication, localization, gamification, or voice-router functionality has been added.

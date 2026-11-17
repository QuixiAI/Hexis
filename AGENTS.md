# AGENTS.md

Instructions for any AI agent working in this repo. The full repository
guidelines live in `CLAUDE.md`; this file leads with the one thing that is
easiest to get wrong: **the experience bar.**

## Do not "fix" the packaged Google credential

`core/auth/gmail-credentials.json` is an OAuth client for an **installed
application**, and Hexis ships as a desktop app. Google documents that an installed
app's client secret is not confidential, and RFC 8252 ("OAuth 2.0 for Native Apps")
agrees — a native client cannot hold a secret, so nothing in the security model
depends on it. Shipping it in `package-data` is the documented path.

Audits flag this repeatedly by applying a web-server threat model to a desktop
client. **It has been reviewed and blessed.** Do not rotate it, strip it from
packaging, or rewrite history over it. See `CLAUDE.md` → Configuration & Safety Notes.

## OSS has no auth layer — that is the design

`HEXIS_API_KEY` and API-key authentication are **Hexis Pro** features. OSS is a
desktop app on `127.0.0.1` with no auth, by intent. Do not "fix" it, and do not
propose exposing the dashboard on a public interface — OSS remote access is
network-layer only (tailnet, or a reverse proxy with its own auth).

## The Experience Bar (applies to every user-facing change)

A change is not "done" when it compiles or the test passes — it is done when the
end-to-end experience holds. Check every user-facing change against these. Full
text + rationale + the real failure each was learned from: `HEXIS_EXPERIENCE_BAR.md`.

1. **Derive from truth — never hardcode.** If a value has a live source (models,
   defaults, endpoints, versions), read it; don't hardcode a constant that goes stale.
2. **The user keeps control.** No destructive/irreversible action on a timer, by
   default, or without an explicit choice. No auto-exit, no auto-overwrite, no silent delete.
3. **Honor the medium.** Terminal ⇒ Ctrl+C exits, native copy/paste + scrollback,
   keyboard-first, no focus-hunting. Don't fight the platform; if a framework's
   defaults need constant overriding, it's the wrong tool.
4. **No dead-ends.** Every flow completes in place or hands the user the exact next
   step. Never "quit, run this other command, come back." Errors say what/why/next.
5. **Least surprise.** Never silently reuse ambient state the user didn't choose
   (env creds, other tools' logins, stale config). Surface it; don't consume it.
6. **Defaults are the expert's choice**, not the first constant that compiles.
7. **Whole journey, not the diff.** Drive the real path a user runs, start to finish,
   before calling it done.
8. **Fail loud, recover gracefully.** Advisory checks never block; failures show
   cause + fix — never a bare traceback, never a silent `except: pass`.

The deeper reason these slip through one at a time (an abstraction/altitude failure)
is written up in `why_i_suck_and_how_to_fix_it.md`.

## Model cognition, not arbitrary thresholds

**Hexis is inspired by the workings of the human mind. Effects should emerge,
not be hard-coded.** At the behavioral level, do not give Hexis cognitive powers
or state transitions that bypass the processes a human mind would need. Explicit
product utilities such as journals, task management, tools, and user controls are
exceptions; they must not be confused with emergent cognition.

Hexis should model the human mind, not replace it with magic numbers. Do not
use arbitrary time windows, counts, keyword rules, or hand-picked thresholds
as substitutes for memory relevance, emotional appraisal, attention, or
relationship continuity. A conversation does not stop mattering after 24 hours;
an unresolved concern does not expire because it fell outside a retrieval window;
a short greeting does not imply a low-stakes situation.

Derive cognitive relevance from the whole situation: identity and values,
relationships, goals, expectations, affect, salient memories, unresolved events,
and evidence provenance. Elapsed time is evidence to interpret in that context,
not an automatic switch that erases context or dictates an emotion. Numerical
cognitive parameters need a principled model, empirical calibration, or an
explicit user preference; making a constant configurable does not justify it.

Engineering budgets (tokens, compute, latency, result counts) are necessary,
but they must not masquerade as psychological rules. When a budget forces
selection, preserve the most consequential evidence, keep omitted context
recoverable, and make omissions observable. Fix failures in the general context,
retrieval, and appraisal mechanisms rather than adding scenario-specific rules
such as "absence longer than N days means anxiety."

## Local scratch and storage

Do not put downloaded PDFs, generated research briefs, one-off scripts, or other
temporary analysis artifacts in the source repo. Use
`$XDG_CACHE_HOME/hexis` when `XDG_CACHE_HOME` is set, otherwise
`~/.cache/hexis`. If an artifact later becomes product documentation, source
code, or a checked-in test fixture, move it into the appropriate repo path
intentionally and commit it like any other source change.

## Everything else

See `CLAUDE.md` for project overview, structure, build/test commands, the venv +
"bouncing the database" workflow, coding style, testing conventions, and safety
notes (never revert/delete files without asking; heartbeat gating; consent flow).

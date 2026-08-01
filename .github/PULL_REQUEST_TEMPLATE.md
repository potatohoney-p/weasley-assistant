**What this does, and why**

**Which issue does it close?** (`Closes #123`)

---

- [ ] **Tested it, not just written it.** Say how below — the review will ask.
- [ ] **A deterministic eval** in `evals/deterministic/` covers the behavior.
      If you fixed a bug, add the case that catches it.
- [ ] `make gate` and `make lint` pass locally.
- [ ] Any heavy or optional dependency is **behind an extra**, not in the
      default install.
- [ ] No hidden network calls, no reading secrets or `.env`, nothing runs at
      install time.

**How you tested it** — commands, and what you saw:

```
```

**Anything you're unsure about?** Say so. A question here is cheaper than a
review round.

---
First PR here? CI needs a maintainer to approve your workflow run. If it looks
stuck, say so on the PR — that delay is ours, not yours.

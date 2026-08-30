# LLM resale pipeline

An autonomous pipeline for hardware resale. Photos go into a Telegram bot; the
system identifies the product, prices it against comparables it has gathered
itself, decides whether the deal clears a profit threshold, negotiates with the
seller, and publishes to MercadoLibre and Facebook Marketplace.

It runs on one refurbished server: Postgres, Redis, six containers, and a local
Ollama (Qwen3 8B and 27B) on a 4 GB Quadro P1000.

**Everything that decides money is deterministic code. The LLM never produces a
number.** That is the central design rule, and the rest of this README is mostly
about what enforcing it costs and buys.

---

## Why rules first, LLM at the edges

A hallucinated price is not a bad sentence — it is a wrong purchase. So the
model is kept off the critical path:

| job | who does it | why |
|---|---|---|
| read brand / model / RAM / disk from a title | `app/extract.py`, regex + tables | 33/33 real titles pass; deterministic and instant |
| read a seller's reply for a price | `app/parseo.py` | handles Chilean slang — "130 lucas", "1 palo" — 14/14 |
| price, ceiling, target, multiple | `app/pricing.py`, `app/specs.py` | pure arithmetic over observed comparables |
| identify a product from photos alone | Qwen3 27B vision | the one place a model is irreplaceable |
| catalogue a title the rules could not | Qwen3 8B | only when rule confidence < 0.6 |
| write negotiation messages | templates | tried the LLM; see below |

The LLM was tried for negotiation copy and removed: from "ThinkPad T480 16GB"
it produced `48016` as if it were money, and one message in three inverted the
roles — offering to *sell* an item the system was trying to *buy*.

Measured split on live data: **80% of titles resolved by rules, 20% escalated to
the 8B.** Every escalation is logged, and that log is the backlog for new rules.

---

## Architecture

```
Facebook Marketplace ──► fetch_fb ──► normalize ──► price_index
  (anonymous browse)                  (rules,        (comparables,
                                       8B at edges)   weighted by source)
                                                          │
MercadoLibre orders ──► ventas_ml ────────────────────────┤
  (what was actually PAID, weight 3)                      │
                                                       score
                                              ceiling = V_liq / 2.0
                                              target  = V_liq / 2.5
                                                          │
                                        ┌─────────────────┴────────────┐
                                    negotiate                       alert
                              (greets, then bargains,          (Telegram, with
                               3 rounds, never past             the three numbers
                               the ceiling)                     and the P50 source)

photos ──► vision (27B) ──► gen_listing ──► publish_ml / publish_fb ──► reply_bot
```

Two Telegram bots: one hunts and negotiates purchases, one takes photos and
publishes listings. Both default to draft mode — nothing goes live without a
button press until you turn that off.

---

## Engineering notes

The interesting part of this project is not that it works; it is the class of
bug that shows up when a pipeline decides money on its own. All of these were
found in production and are documented in the commit history:

**Silent failure is the dominant failure mode.** Five things broke at once and
none of them raised an error: an OAuth token expired because a scope was missing
from the authorization URL, and the status command still reported "connected";
the account-safety lock was applied to a read-only job and killed the only data
source; the scoring job's `LIMIT 200` meant that with 444 candidates the oldest
244 were *never evaluated*, because an item that is judged and rejected leaves
no row and stays a candidate forever. Every fix in this repo includes making the
failure visible, not just making it stop.

**A shelf key that does not capture what kind of thing it is will lie in both
directions.** `UNIQUE(brand, model)` put an 8GB/256 ThinkPad and a 32GB/1TB one
on the same shelf, so the median described neither. Fixed with a two-level index
(exact spec tier when it has ≥3 samples, otherwise the model median corrected by
a single composite score — `log2(RAM) + 0.5·log2(disk)`, slope measured in the
product's own data). Two independent coefficients were tried first and were
worse: RAM and disk are collinear, so each stole the other's credit.

The same bug reappeared twice more in different clothes. A laptop that mentions
its GPU was being filed *as* that GPU: the "RTX 3050" shelf held two gaming
laptops at 550k and 650k next to the actual card at 160k, so the system flagged
a fairly-priced card as a bargain. And a charger "for ThinkPad T480" was filed
as a T480, dragging the laptop's median *down* — which produces no false alerts
at all, just silently stops producing true ones.

**Scraping location is a correctness problem, not a config detail.** The
Marketplace city slug was wrong by one word; Facebook silently ignored it and
served a different country. 75 of 527 collected listings were priced in US
dollars. There is now a currency guard and an alert when a whole cycle comes
back foreign.

**Every queue needs a proven consumer.** A "Send" button pushed an id onto Redis
that nothing read, so in the default manual-approval mode 100% of drafted
replies went nowhere.

---

## Safety

- **The personal Facebook account is never automated.** The lock reads the
  `c_user` cookie rather than the on-screen name — a lock that depends on
  Facebook's layout opens itself the week they change it — requires a typed
  approval to register an account, honours a blacklist, and fails closed.
- **Hunting runs with no session at all.** Reading public listings touches no
  account, which is strictly safer than browsing logged in; the lock still
  blocks any session that is not the approved one.
- Publishing and negotiation default to draft mode, with per-day caps and
  human-paced delays.
- OAuth uses PKCE and validates `state`, so a callback URL cannot connect the
  system to somebody else's account.

## Tests

Deterministic, no network, no model, no database:

```
bin/test_extract.py   33/33   real listing titles
bin/test_parseo.py    14/14   seller replies, Chilean slang
bin/test_fb.py        51/51   account lock, read lock, alert de-duplication,
                              card parsing, foreign-currency rejection
bin/test_specs.py       OK    the spec-tier price index, on real cases
```

## Running it

```bash
cp .env.example .env      # fill in your own credentials; .env is gitignored
docker compose up -d --build
bin/cazador test          # all four suites
bin/cazador logs worker
```

Configuration lives in `config/` — every number that affects money is in
`policy.yml`, and every Facebook selector is in `facebook.yml`, so a layout
change is a YAML edit rather than a code change.

## Status

Written and put into production in August 2026. It hunts continuously, builds
its own price index, and alerts on listings that clear the threshold. Automated
purchasing is implemented and tested but is not yet running unattended — the
negotiation path requires a live MercadoLibre session, and purchases still go
through a human button press by design.

`PLAN.md` (Spanish) is the working document: the full architecture, every
production incident, and what was measured rather than assumed.

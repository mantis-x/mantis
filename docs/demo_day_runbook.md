# Demo Day runbook

> Read this the morning of July 2. Do not skip any step.

## Pre-demo checklist (do the night before)

- [ ] All workers running on Railway/Fly.io — confirm with `make health`
- [ ] At least one real signal in the `signals` table with `audit_tx_hash` set
- [ ] At least one agent execution logged in `executions` table
- [ ] Mantle explorer bookmarks open:
  - `SignalAuditLog` contract address → "Events" tab
  - `AgentIdentity` contract address → "Events" tab
- [ ] Telegram bot responding to `/status` command
- [ ] 90-second backup demo video downloaded locally (not in browser)
- [ ] Screen share tested — correct window selected, no notification banners
- [ ] `.env` on production server does NOT contain demo wallet private key

## Screen layout during demo

Three windows, pre-arranged:
1. Telegram (show signal card received by bot)
2. Mantle explorer (audit log contract events)
3. Mantis Execute agent identity page (decision history)

## Demo script (90 seconds, live)

1. Open Telegram → show a real signal card from the last 24h
2. Read the confidence score and key factors aloud
3. Switch to Mantle explorer → find the matching audit tx hash
4. Say: "Every signal is hashed and recorded here — immutable, verifiable"
5. Switch to Mantis Execute identity page
6. Show the agent's decision log — matching signal_id, execution tx hash
7. Say: "One signal. Zero clicks. Full loop — intelligence to execution, on Mantle."

## If the live demo fails

1. Stay calm. Say: "Let me show you the recording while the node reconnects."
2. Open the local backup video immediately.
3. Narrate over the video — do not go silent.
4. Never apologise more than once.

## Post-demo

- Post the X thread immediately after your slot ends (Community Voting)
- DM Byreal and Mirana Ventures judges on X — thank them, share GitHub

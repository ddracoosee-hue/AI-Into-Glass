# Questions for the Even Hub Discord

Ask these before submitting the `.ehpk`.

## 1. Network whitelist for a backend that differs per user

Approval Gate's backend is a relay on each user's own PC, reached over Tailscale at an address like
`https://<pc>.<tailnet>.ts.net`. The `network` whitelist is fixed into `app.json` when the `.ehpk` is
packed. Every user's relay has a different `*.ts.net` address, so a single published build can't
whitelist them all.

> Does the `network` whitelist accept a wildcard such as `https://*.ts.net`? If not, is there a
> supported way for a plugin to reach a host the user configures at runtime, or do you expect each
> user to build and sideload their own `.ehpk`?

**Fallback if the answer is no:** each user builds their own `.ehpk` with their own `*.ts.net` host in
the whitelist. The README documents that build step. The published plugin would then be source-only:
the GitHub repo plus build instructions, with no store listing.

## 2. Review of a plugin whose backend is the user's own PC

From the build plan. The plugin talks only to a relay the user runs on their own machine. There is no
server we operate.

> Does Even's review accept a plugin whose only backend is software the user runs on their own PC?

# Traffic generation — a hard prerequisite, not a nice-to-have

The prompts in Challenge 2 and Challenge 4 are only as good as the telemetry
behind them:

- *"What are the top 10 most queried domains in the last 24 hours, and which
  clients are generating the most NXDOMAIN responses?"* (Challenge 2)
- *"Chart DNS query failures for the last 24 hours and show the drop after my
  fix."* (Challenge 4)
- *"Project which DHCP pools will hit 90% utilization in the next week at the
  current rate."* (Challenge 4)

None of these are answerable from raw record reads. They come from analytics
cubes, which means there has to be real query volume in the tenant, with a
realistic mix of NXDOMAIN, SERVFAIL, latency and QPS patterns, and it has to
predate the participant's session by long enough for a 24-hour window to be
meaningful.

Without it the agent's answers come back thin or empty and the whole "the agent
is querying live tenant data, not summarizing documentation" teaching beat
collapses. **Validate that the DNS activity and host-metrics cubes return
sensible data before finalising any challenge prose.**

## Source

The `iq-insighter` traffic-generator work tracked in
[TME-787](https://infoblox.atlassian.net/browse/TME-787) is the natural source.

## TODO-19 — what this directory needs

Answer these, then add the generator invocation here and call it from
`track_scripts/setup-shell` immediately after `seed_lab.py`:

1. **How is `iq-insighter` invoked?** Container image, CLI, or Terraform module?
   What does it need to point at a tenant — a Service API key, a resolver IP,
   both?
2. **How long must it run before the cubes are populated?** This sets whether it
   runs at track start (and the participant waits) or against a long-lived
   pre-warmed tenant. If a 24-hour window is genuinely required, per-participant
   ephemeral tenants cannot satisfy Challenge 2 and 4 as written, and the
   telemetry has to come from a shared pre-seeded tenant instead. **This is the
   single biggest open design risk in the track.**
3. **Which query patterns does it need to produce** so that:
   - `app.techcorp.internal` shows intermittent SERVFAIL/NXDOMAIN consistent
     with INC-4471,
   - one identifiable client dominates the NXDOMAIN count for the Challenge 2
     prompt,
   - there is a visible drop in failures after the Challenge 3 fix, which is
     what the Challenge 4 chart is supposed to show.
4. **Does the generator need to keep running during the session?** Challenge 4's
   "show the drop after my fix" implies yes — the fix has to change the curve
   while the participant watches.

## Related

`lab_config.PATHS["dns_activity_cube"]` (TODO-16) is the API side of the same
question: the checks need the REST path, time-window parameter and response
shape for the same data the prompts ask about.

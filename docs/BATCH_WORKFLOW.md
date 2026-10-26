# A repeatable batch workflow

[Back to README](../README.md)

The largest completed configuration run covered 50 tags. A 550/600-tag deployment remains unvalidated. The workflow below is a proposal to reduce handling and make exceptions visible.

## Prepare once

- Freeze the intended profile and test it on a small pilot, including reconnect verification and rollback.
- Use one Windows laptop and one running Bluetooth job. Keep the phone app disconnected.
- Label physical trays and associate each with a CSV inventory and state JSON. Suggested tray size: 25–50 tags; this is an organizational choice, not a measured capacity limit.
- Spread tags in one layer near the laptop. Avoid a tightly stacked pile.
- Keep the same power source and laptop sleep settings throughout a run.

## For each tray

1. Scan to a new inventory filename. Compare the count with the physical tray and enable only intended addresses.
2. Run `plan` with the explicit profile. Investigate unsupported devices and unexpected frame types.
3. Apply using `--auto-retry` and a tray-specific state filename.
4. Check the progress JSON for the full inventory, including `not_attempted`. A completed process is not necessarily a completed tray.
5. Resume temporary failures using the same command. Put persistent exceptions in a labelled physical group while retaining unresolved entries in the ledger.
6. Resolve exceptions, using one-tag startup recovery only when necessary.
7. Mark the tray complete only after every selected tag is `verified`. Keep a copy of its inventory, profile and state together.
8. If deployment depends on gateway reception, also observe live receiver traffic for the expected frame types. Configuration readback and reception are different checks.

```powershell
.\minew.cmd scan --seconds 30 --output tray01.csv
# Edit tray01.csv: enable only intended addresses.
.\minew.cmd plan --inventory tray01.csv --profile profile_acc_1s.json --default-password --output tray01_plan.json
.\minew.cmd apply --inventory tray01.csv --profile profile_acc_1s.json --default-password --state tray01_state.json --auto-retry
```

## Retry policy

Automatic mode processes the inventory, then revisits eligible transient failures. Defaults are two extra passes with 30- and 60-second delays. Three consecutive transient connection failures pause the run for inspection. Configure these with `--retries`, `--retry-delay` and `--failure-limit` if needed.

Confirmed mismatches, partial writes and hard validation failures are not blindly retried by the delayed queue. A later deliberate resume reads and reconciles the saved transaction. Do not keep launching new state files against a tag with an unresolved write.

## Before scaling up

Record the number of first-pass successes, delayed successes, battery interventions and persistent exceptions. Record elapsed time per tray. Use those measurements to adjust the workflow; the earlier 50-tag run does not establish throughput for 550 tags.

Do not use the experimental storage profile as a default rollout profile. Its battery benefit has not been measured, and reconnect retries were required in the small trial.

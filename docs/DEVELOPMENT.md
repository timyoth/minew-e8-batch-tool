# Developer guide

[Back to README](../README.md)

## Source map

| File | Responsibility |
|---|---|
| `batch_configure.py` | CLI, inventory, state ledger, transactions and rollback. |
| `batch_workflow.py` | Delayed retry passes, progress reporting and startup recovery. |
| `configure_tag.py` | Windows BLE session, authentication and low-level reads/writes. |
| `connectivity.py` | Rules that preserve a configuration connection path. |
| `profiles.py` | Profile validation, target construction and transition guards. |
| `inspect_tags.py` | Supporting inspection utilities; see its `--help`. |
| `test_*.py` / `tests_data/` | Offline regression tests and synthetic fixture. |

Install with `setup.cmd`; `minew.cmd doctor` checks local prerequisites without scanning or connecting. Output-path validation runs before Bluetooth access to protect inventory/profile files from derived status exports. Then run the suite from the repository root. Use `minew.cmd --help` and each subcommand's `--help` as the CLI reference. The public source does not include the local bootstrap key; see the installation guide before attempting configuration.

## Transaction model

1. Authenticate and check the model/firmware.
2. Read all six slots and connection modes.
3. Construct and validate both the forward and restoration transitions.
4. Save the original snapshot and an applying checkpoint before the first configuration write.
5. Apply only differences, preserving access and using the required frame/interval write order.
6. Compare a full readback while connected, then save a pending-verification checkpoint.
7. Disconnect and verify through a new session.

A transient timeout after a successful connected readback leaves `pending_verify`; it does not cause an immediate destructive-looking cycle of writes and restoration. Confirmed mismatches or incomplete writes trigger restoration. An interrupted process leaves durable state for reconciliation rather than attempting emergency writes during cancellation.

The first baseline in a ledger is retained across later attempts. Restoration of an individual failed attempt and user-requested rollback therefore have different context: rollback restores that first baseline, while attempt recovery can restore its saved pre-attempt state.

State writes use a temporary file, flush/fsync and replacement. Windows locks protect a ledger and the CLI installation's Bluetooth job. They do not prevent interference from a phone app, another installation or independent scripts.

## Extending safely

- Preserve the explicit MAC inventory and model/firmware guard.
- Keep snapshots and target generation separate from I/O.
- Validate restoration as well as forward changes before writing.
- Test interruption at transaction boundaries and distinguish unavailable verification from a confirmed mismatch.
- Do not add a new frame type by changing its identifier alone; its required payload/identity must also be valid.
- Repeat hardware tests on a small recoverable set before deploying changes broadly.

Slot numbers in profiles are 1–6; low-level calls use 0–5. Active intervals are bounded by profile validation; disabled slots are handled through explicit transitions.

## Repository hygiene

Do not commit `sdk_bootstrap_key.bin`, passwords, live inventories, result ledgers, local environments or field recordings. `.gitignore` excludes the usual local artifacts; review new filenames before committing. Example MACs are illustrative, not deployment targets.

No software license has been selected for this initial source package. Repository visibility alone does not grant a particular redistribution license; the owner should choose one before presenting this as a licensed open-source release.

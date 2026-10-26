# Recovery and troubleshooting

[Back to README](../README.md)

Keep the original inventory, profile and state JSON together. The state file is the recovery record, not a disposable log. Do not delete it to make an error disappear.

## Understand the result

| Status | Meaning / next action |
|---|---|
| `verified` | Full target confirmed. Ordinary resume skips this tag. |
| `pending_verify` | Writes read back correctly, but a fresh connection could not confirm them. Resume to read before writing again. |
| `applying` | A durable checkpoint exists; the previous process may have stopped during a write. Resume with the same state. |
| `failed` | Inspect the error. `rollback_verified=true` means restoration was verified, not that the requested profile succeeded. |
| `needs_recovery` | A change or restoration remains unresolved. Keep the tag in the inventory. |
| `restoring` | Rollback started but has not completed. Repeat rollback. |
| `rolled_back` | Original snapshot restored and checked. |
| `not_attempted` | Progress report only: no attempt has yet been made for this inventory entry. |

## Start with connection checks

1. Disconnect the tag in the Minew app and close the app.
2. Keep tags close, separated and powered. Check batteries and isolation tabs.
3. Stop other Bluetooth jobs. If Windows reports adapter/handle errors across several tags, stop the tool before toggling Bluetooth off/on.
4. Repeat the original command using the same files.

Automatic mode pauses after repeated connection failures. It does not reset the adapter or repeatedly cycle power. A validation error, unsupported firmware or partial-write recovery problem should be inspected rather than treated as ordinary radio interference.

For a read-only check, use `plan`. `apply --recheck` may write corrections.

## Resume the batch

```powershell
.\minew.cmd apply --inventory tags.csv --profile profile_acc_1s.json --default-password --state acc_state.json --auto-retry
```

Keep unfinished tags enabled in the inventory. The tool refuses a ledger whose unresolved tags have been removed from the selection. A state file is bound to the profile's settings; changing its profile requires a new state file, after resolving the previous transaction.

## Restore the original settings

```powershell
.\minew.cmd rollback --inventory tags.csv --profile profile_acc_1s.json --default-password --state acc_state.json
```

This restores each tag's first original snapshot in this state file, not the most recent app settings. It stops on failure or a missing baseline. For a partial batch, select tags with saved baselines, but retain every unresolved tag required by the ledger. Repeat rollback after fixing a connection problem.

## Battery-startup recovery for one tag

Use this when a tag with an existing state record is not accepting connections. It is not a factory reset.

1. Close the phone app. Remove the affected tag's battery and leave it out.
2. Start the command below, replacing the illustrative MAC with the affected tag's full address. That address must be enabled in the inventory and already recorded in the state file.
3. Wait for **SCANNER READY** before reinserting the battery.
4. Leave the tag near the laptop until the result is printed.

```powershell
.\minew.cmd recover --inventory tags.csv --profile profile_acc_1s.json --default-password --state acc_state.json --address C3:00:00:00:00:01 --startup-seconds 300
```

Recovery watches for a current connectable advertisement from only that MAC, then reads and reconciles its state before resuming application of the selected profile. It is an apply-recovery command; if your goal is to undo the experiment, use explicit rollback afterward. A scan timeout makes no settings changes.

## Common errors

| Problem | Action |
|---|---|
| Missing `sdk_bootstrap_key.bin` | Supply the authorized 16-byte local file described in the README. |
| Wrong password | Omit `--default-password` and enter the tag's eight-character password. |
| Model or firmware rejected | Do not remove the guard; this version has only been validated for Beacon Plus-BL / 2.3.17. |
| Empty iBeacon slot | This tool cannot create an iBeacon identity. Configure a valid UUID/major/minor in the vendor app first. |
| Unsupported interval/power | Correct the profile; active advertising intervals cannot be zero. |
| State/profile mismatch | Use the original profile to finish or restore the transaction before starting a separate experiment. |
| Lock in use | Check for another running process. Do not run simultaneous jobs against the same tags. |

A disabled slot can legitimately contain a zero interval internally. Zero is not a valid active advertising interval. The writer uses a specific disable/enable order and connection preservation rules; bypassing those rules with raw characteristic writes is outside this tool's supported workflow.

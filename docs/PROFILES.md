# Profile reference

[Back to the main page](../README.md)

A profile describes the desired state. It is not a sequence of raw Bluetooth writes. Unspecified settings are retained, except that connection modes can be adjusted to keep a surviving active slot connectable.

## Example: ACC every second

```json
{
  "version": 1,
  "name": "ACC every second",
  "slots": [
    {"slot": 2, "type": "acc", "interval_ms": 1000, "tx_dbm": -4}
  ]
}
```

| Field | Meaning |
|---|---|
| `version` | Required; must be `1`. |
| `name` | Optional descriptive label; does not affect the profile hash. |
| `slots` | Required nonempty list of unique slot rules. |
| `slot` | App slot number, **1–6**. |
| `type` | `preserve` (default), `acc`, or `disabled`. |
| `require_frame` | Optional guard: `ibeacon`, `acc`, `disabled`, or `other`. |
| `interval_ms` | Integer 100–5,000 for active slots. |
| `tx_dbm` | One of −40, −20, −16, −12, −8, −4, 0 or +4; also checked against the tag's capabilities. |

Unknown fields and duplicate slots are rejected. `acc` requires both interval and power. It can replace only an empty or ACC slot; overwriting iBeacon or another frame type is rejected.

`preserve` retains the frame type and can change the interval/power on an active slot. It cannot configure an empty slot; select `acc` explicitly to enable one. Creating an iBeacon identity in an empty slot is not implemented.

## Change an existing iBeacon's power

```json
{
  "version": 1,
  "slots": [
    {"slot": 1, "type": "preserve", "require_frame": "ibeacon", "tx_dbm": 4}
  ]
}
```

The UUID, major/minor identifiers and other unspecified values remain unchanged.

## Disable ACC

```json
{
  "version": 1,
  "slots": [
    {"slot": 2, "type": "disabled"}
  ]
}
```

Do not supply interval or power with `disabled`. The writer handles the empty-frame/zero-interval sequence internally and preserves a usable connection path. Zero is not accepted for an active slot. Disabling broadcasts does not prove the accelerometer itself is powered down.

## Profile hashes and restoration

The state file is bound to the effective profile. Renaming the profile or reordering its slot rules does not change that hash; changing actual settings does. Use a new state path for a new profile. Rollback requires the profile associated with the saved state and restores the first baseline in that file—not an assumed factory configuration.

## Experimental storage profile

`profile_storage.json` retains the existing Slot 1 iBeacon identity, sets it to 5,000 ms / −4 dBm, disables Slot 2, and slows an existing `other` frame in Slot 5 to 5,000 ms without changing its power. That Slot 5 requirement makes this profile specific to a matching layout. Preview first.

The trial required connection retries, and battery savings were not measured. Five seconds is the current tool's supported interval ceiling, not a claim about every firmware variant. Keep the baseline and verify a storage/restore round trip before relying on this profile. Normal field-test use should start from `profile_acc_1s.json` instead.

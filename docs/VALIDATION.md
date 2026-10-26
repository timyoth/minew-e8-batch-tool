# Validation record and limits

[Back to README](../README.md)

These are observations from the development trials in September 2026, not a live audit of the tags today. Raw device inventories and field recordings are not included in this repository.

## Hardware observations

| Trial | Observed result | Limits |
|---|---|---|
| 10-tag transmit-power change | Both main slots changed to +4 dBm and verified through reconnects. | Tested firmware and existing frame identities only. |
| 10-tag temporary slot | ACC added to Slot 6 at 1,000 ms / 0 dBm, then removed/restored and verified. | Not proof that arbitrary frame types can be created. |
| 50-tag ACC deployment | Slot 2 ACC at 1,000 ms / −4 dBm eventually verified on all 50. | 16 initial exceptions; retries/repositioning and battery intervention for the final two were needed. |
| Seven-tag storage trial | Seven changed tags restored to exact normal snapshots and reconnect-verified; other 43 unchanged. | Partial trial only; no measured battery benefit. |
| Live broadcast audit | A later 120-second scan observed both iBeacon and ACC from all 50. | Observation window only; not a packet-delivery or battery-life guarantee. |

## Offline regression suite

The 48 tests use mocked Bluetooth sessions and synthetic snapshots. They cover profile/inventory validation, transaction checkpoints, write ordering, connection preservation, reconnect mismatch handling, rollback, retries, interruption and current Windows advertisement selection.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s . -p "test_*.py" -v
```

These tests do not open a real tag connection and do not require the local bootstrap key. Passing them cannot establish radio reliability or firmware behavior on a different device version.

## Important Windows discovery fix

The installed Bleak Windows backend may retain the first advertisement in a device object's details. A tag can send both connectable and non-connectable frames. Treating that first packet as current caused false failures when a later packet was connectable; it also made a raw audit appear to see only one frame type.

The scanner now reads the current callback's Windows advertisement events, checks the intended MAC and uses the current connectable event/address type. Tests cover stale versus current events and rejecting another MAC. This fixes a specific discovery error; it does not explain or eliminate every connection timeout.

## What has not been established

- A 550/600-tag production run or a guaranteed throughput rate.
- Zero-risk firmware changes or recovery from every failure.
- Support for other firmware, models or operating systems.
- Battery lifetime, storage-mode savings or transmission range from controlled measurements.
- Gateway packet delivery simply because a configuration readback passed.

The protocol implementation should be treated as version-specific. Retain the device guard and repeat small hardware trials before broadening compatibility.

## Repository usability review (26 October 2026)

A fresh installation and CLI checks were performed separately from the earlier hardware trials. The review added local bootstrap diagnostics (`doctor`), output/input collision guards, refusal to overwrite plan files, and early rejection of null profile values. These changes have offline regression coverage. Fresh setup completed under Python 3.12.10; `pip check`, all 48 tests, all command help screens, six profile forward/restoration checks against the fixture, and a five-second live discovery scan passed. Missing-key diagnostics were verified. No tags were connected to or reconfigured during this review; live apply/rollback results remain the historical trials above.

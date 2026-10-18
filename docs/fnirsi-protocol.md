# FNIRSI DPS-150 backend

The backend uses USB CDC `2e3c:5740`, 115200 baud, 8N1, RTS asserted, and no
RTS/CTS handshake. All I/O, including connection and cleanup, runs in the
controller worker. Hardware identification is USB metadata followed by a
model read requiring `DPS-150`; there is no probing of unrelated ports.

Frames are `HEADER COMMAND REGISTER LENGTH PAYLOAD CHECKSUM`, with TX header
`F1`, RX header `F0`, and checksum `(REGISTER + LENGTH + sum(PAYLOAD)) & 255`.
Header and command are excluded. Floats are little-endian IEEE-754 binary32.
GET uses command `A1` and a one-byte zero payload; SET uses `B1`.

| Function | Register/payload |
| --- | --- |
| Remote open/close | Command `C1`, register 0, byte 1/0 |
| Model | `DE`, ASCII identifier |
| Active voltage/current | `C1/C2`, float32 V/A |
| Measured voltage/current/power | `C3`, three float32 values |
| OVP/OCP thresholds | `D1/D2`, float32 V/A |
| Output | `DB`, byte 0/1 |
| Available voltage/current | `E2/E3`, float32 V/A |
| Full state | `FF`, 139-byte packed payload |

Full-state offsets: setpoints 4/8; OVP/OCP 76/80; output 107; available limits
111/115; OVP/OCP ceilings 119/123. Many floats are unaligned. Byte 110 is
unknown and retained as raw data. The codec can decode a shorter core, but the
GUI requires the protection ceilings to be present before enabling controls.

The original Windows app's baud command mutates its payload without updating
its checksum. This backend omits that unnecessary command. It never sends the
firmware BOOT command (`C0`) and does not implement flashing.

Requests are serialized and paced at least 50 ms apart; replies can interleave
with pushed telemetry. The parser retains partial frames and validates known
payload sizes and checksums. A plausible corrupt length can delay recovery
until timeout; stale telemetry causes disconnection. No automatic write retries
are made, since a timeout cannot establish whether the PSU applied a write.

Connection reads state and switches output off with readback, without changing
setpoints/protection thresholds. Output enable verifies setpoints first. Normal
cleanup writes output-off, releases the session, and closes the serial handle;
it never writes stale GUI setpoints or alters protection thresholds.

The protocol exposes no confirmed independent OCP/OVP enable switches or special
disabled threshold. The inline editors therefore write only positive finite
values within reported ceilings, one field at a time. A failed threshold
readback triggers a state refresh and visible error, without retry or rollback.
Setpoints use device-reported limits capped at the nominal 30 V / 5 A rating.

Sources inspected: the supplied FNIRSI WinForms EXE (assembly version 1.0.0.0,
SHA-256 `dfa0c7b316345b4a6987d28473aae0bd2d93e200deef79dbae8502b68cdf4570`),
[cho45](https://github.com/cho45/fnirsi-dps-150),
[KochC](https://github.com/KochC/DPS-150-python-library),
[svenk123](https://github.com/svenk123/dps150tool), and
[hmldns](https://github.com/hmldns/dps150ctl).

The two V1.2 capture fixtures were copied from hmldns commit
`9edd13ae51adb35dede5a7a975a65b07c0883cb2`; its MIT license is included beside
them in `tests/fixtures/LICENSE`. Capture replay covers every single split point
and the recorded USB-read boundaries. Serial simulations additionally exercise
readbacks, wrong models, changing limits, partial frames, and timeouts. GUI tests
render headless frames using the actual imgui-bundle binding. These are not
claims of physical device verification, USB bus capture, or rendered GPU screenshots.

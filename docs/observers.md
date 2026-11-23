# Observing an lcftp plan with your own software

If lcftp can drive your telescope directly (see the drivers under
`drivers/`), `lcftp run` does everything. If it can't, you can still
observe lcftp targets: export the plan as a CSV file, carry it out with
N.I.N.A. or whatever software you use, and send the frames back.

## Getting a plan

Ask for a plan covering the time your telescope is available, then
export it:

```bash
lcftp plan --start 2026-11-24T01:00:00Z --end 2026-11-24T09:00:00Z
lcftp export tonight.csv
```

`lcftp export` with no file name prints the CSV. `--plan-file plan.json`
exports a saved plan instead of the current one.

## What's in the file

One row per target, in the order they should be observed.

| Column | Meaning |
|---|---|
| `Familiar Name` | The **target tag**, e.g. `lcftp-3f9a1c2e-007`. See below. |
| `Catalogue Entry` | The Rubin `diaObjectId`, as `diaObject <id>` |
| `Right Ascension (J2000)` | e.g. `05h 35m 17.30s` |
| `Declination (J2000)` | e.g. `-05d 23m 28.0s` |
| `Position Angle (East)` | Always `0`; lcftp has no rotation preference |
| `Not Before (UTC)`, `Not After (UTC)` | The window this target is planned for |
| `RA (deg)`, `Dec (deg)` | The same position in decimal degrees |
| `Filter` | Suggested filter, if any |
| `Total Exposure (s)` | Suggested total integration time |
| `Target SNR` | Suggested signal-to-noise, if given instead |
| `Mag`, `Band` | Brightness at the latest Rubin detection |
| `Classification` | Broker classification, if any |
| `Priority` | 0 is the most wanted target |
| `Dia Object ID`, `Plan ID`, `Item ID` | Identifiers, for reference |

## The target tag

Every target has a tag like `lcftp-3f9a1c2e-007`: the first eight
characters of the plan id, then the item number. **The tag is how lcftp
knows which target a frame belongs to**, so it has to end up in each
frame, either:

- in the file name (anywhere; case doesn't matter), or
- in the FITS `OBJECT` header.

Frames without a tag can't be matched automatically.

## Using N.I.N.A.

1. In the Simple Sequencer, choose **Import targets** and pick the CSV.
   NINA reads the names and coordinates; it replaces the current target
   list and applies your default sequence template to each target.
   Exposures come from that template, so use the `Filter` and
   `Total Exposure (s)` columns as a guide when you set it up. The
   Target Scheduler plugin can import the same file (it is in
   Telescopius format).
2. In **Options → Imaging → Image file pattern**, make sure the pattern
   includes `$$TARGETNAME$$`. The target name is the tag, so every
   frame's file name then carries it.
3. Observe each target inside its UTC window as far as you can.

## Using other software

Enter the targets from the CSV however your software prefers, and name
each target with its tag, or otherwise make sure the tag gets into each
frame's file name or `OBJECT` header. Keep to the UTC windows.

## Sending frames back

*Coming soon: `lcftp ingest` will collect tagged frames from a
directory and queue them for `lcftp upload`. Until then, keep your
frames with their tagged names.*

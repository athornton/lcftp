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

Point `lcftp ingest` at the directory your software saved frames in
(subdirectories are searched too), then upload:

```bash
lcftp ingest ~/Pictures/NINA/2026-11-24
lcftp upload
```

Ingest copies each tagged FITS frame (`.fits`, `.fit`, `.fts`) into the
lcftp spool and records which target it belongs to. **Your original files
are never moved or changed.** You can run it as often as you like:
frames it has already taken are recognised and skipped.

Add `--dry-run` to see what would happen without copying anything.

If your software can't put the tag in file names or headers, put each
target's frames in their own directory and name the target explicitly:

```bash
lcftp ingest --item lcftp-3f9a1c2e-007 ~/frames/target7
```

`--item` only applies to frames with no tag of their own.

### When a frame is not taken

Ingest prints one line per file. Anything other than `ingested` means
the frame was left alone:

| Status | Meaning |
|---|---|
| `already_ingested` | This exact file was ingested before |
| `already_lcftp` | The frame came from an lcftp driver and is already in the spool |
| `no_tag` | No tag in the file name or `OBJECT` header (see `--item`) |
| `conflicting_tags` | The file name and `OBJECT` header name different targets |
| `unknown_plan` | No plan this client knows starts with that id |
| `unknown_item` | The plan has no target with that number |
| `wrong_observatory` | The plan belongs to a different observatory |
| `bad_headers` | Not readable FITS, or no usable `DATE-OBS` or exposure time |
| `outside_window` | `DATE-OBS` is outside the plan's whole time window |
| `off_target` | The pointing in the headers is more than one field of view from the target |

`outside_window` and `off_target` are there to catch mistyped or reused
tags. If you are sure the frame is right, `--force` accepts it anyway.
A frame taken outside its own target's window, but within the plan's,
is accepted with a warning: starting late is fine.

### Finishing a plan

When you've ingested everything you are going to for a plan, close it:

```bash
lcftp ingest --final --plan 3f9a1c2e
lcftp upload
```

This tells lcftp which targets you observed and which you didn't. Every
target with frames is reported as observed; every other target is
reported as skipped. **Until you do this, nobody knows which targets
were missed**, and the outcome can only be reported once, so run it
when you're done, not before. `lcftp status` lists plans that still
need `--final`.

You can combine the last ingest and the finish:
`lcftp ingest --final --plan 3f9a1c2e ~/frames`.

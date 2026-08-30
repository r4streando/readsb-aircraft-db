# readsb-aircraft-db

Builds freshness-aware aircraft databases for readsb and VDLm2 Monitor by merging Wiedehopf's readsb/tar1090 aircraft CSV with ADS-B Exchange's `basic-ac-db.json.gz`. Both projections are produced from one shared reconciliation pass.

Core policy:

- union every valid ICAO address known to either source;
- a blank value never replaces a known value;
- PIA/LADD flags **never** suppress registration or other aircraft metadata;
- when both sources contain different nonblank values for registration, ICAO type, year, or owner/operator, the snapshot with the newer gzip-header timestamp wins;
- Wiedehopf long descriptions are retained when available, otherwise ADSBX manufacturer + model fill the readsb description field;
- military is retained when either source marks the aircraft military;
- Wiedehopf's `interesting` flag is retained;
- output uses readsb's native format: `ICAO;registration;icaotype;flags;description;year;owner/operator;`.

The readsb projection retains the existing format and merge behavior. The VDLm2 projection is plain UTF-8 newline-delimited JSON, sorted by ICAO, and contains the same union of valid ICAOs. Every VDLm2 record has exactly these fields:

```text
icao, reg, icaotype, year, manufacturer, model, ownop,
faa_pia, faa_ladd, short_type, mil
```

Unknown strings and years are JSON `null`; flags are booleans and valid years are integers. ADSBx is the only source for manufacturer, model, and short type. Those three fields are conservatively suppressed when a newer Wiedehopf snapshot wins a conflicting nonblank registration or ICAO type, because the ADSBx descriptors may belong to the displaced airframe identity. Wiedehopf descriptions are never heuristically split.

## Install on the readsb host

Because this repository is private, clone it using the Pi's authenticated GitHub access. With GitHub CLI:

```bash
gh repo clone r4streando/readsb-aircraft-db
cd readsb-aircraft-db
sudo bash install.sh
sudo /usr/local/sbin/update-readsb-aircraft-db
```

Or with an authenticated SSH key:

```bash
git clone git@github.com:r4streando/readsb-aircraft-db.git
cd readsb-aircraft-db
sudo bash install.sh
sudo /usr/local/sbin/update-readsb-aircraft-db
```

Then add to the readsb decoder options:

```text
--db-file /var/lib/readsb-aircraft-db/aircraft.csv.gz --db-file-lt
```

Restart readsb once after changing its command-line configuration. The updater thereafter atomically replaces the database file; current readsb detects database mtime changes and reloads it.

## Automatic updates

The installer places `/etc/cron.d/readsb-aircraft-db`, which runs daily at 03:17 local time. The updater downloads both sources fresh on every run, validates them, merges them, writes an audit report/conflict file, keeps rolling backups, and does not replace the installed database if its uncompressed content is unchanged.

Source snapshots:

- Wiedehopf: `https://raw.githubusercontent.com/wiedehopf/tar1090-db/csv/aircraft.csv.gz`
- ADS-B Exchange: `https://downloads.adsbexchange.com/downloads/basic-ac-db.json.gz`

## Data path

Generated databases:

```text
/var/lib/readsb-aircraft-db/aircraft.csv.gz
/var/lib/readsb-aircraft-db/vdlm2-aircraft.json
```

The updater validates both candidates before changing either installed output. Each changed file is staged alongside its destination and atomically renamed into place. Content hashes and mtimes are handled independently: readsb uses the uncompressed SHA-256, while VDLm2 uses the ordinary file SHA-256. Backups are separately scoped. The updater does not restart either consumer; VDLm2 Monitor must be restarted or gain its own hot-reload support before a changed database is loaded.

Audit artifacts:

```text
/var/lib/readsb-aircraft-db/last-report.json
/var/lib/readsb-aircraft-db/last-conflicts.csv.gz
/var/lib/readsb-aircraft-db/backups/
```

Environment overrides include `DEST`, `VDLM2_DEST`, `BACKUP_DIR`, and `VDLM2_BACKUP_DIR`.

## Tests

The test suite uses only generated local fixtures and temporary directories:

```bash
python3 -m unittest discover -s tests -v
```

It covers frozen decompressed readsb output, union projection behavior, JSON null/type rules, conservative descriptor suppression, independent unchanged mtimes, and failure-before-publication behavior.

## Validation baseline

Tested against the supplied August 24 / August 29, 2026 snapshots:

- output rows: 616,480
- registrations: 614,893
- descriptions: 498,948
- PIA records with registrations: 50,423 / 50,423
- LADD records with registrations: 30,315 / 30,315
- malformed readsb output rows: 0
- logged source conflicts/flag changes: 14,637

# readsb-aircraft-db

Builds a freshness-aware, non-destructive `aircraft.csv.gz` for readsb by merging Wiedehopf's readsb/tar1090 aircraft CSV with ADS-B Exchange's `basic-ac-db.json.gz`.

Core policy:

- union every valid ICAO address known to either source;
- a blank value never replaces a known value;
- PIA/LADD flags **never** suppress registration or other aircraft metadata;
- when both sources contain different nonblank values for registration, ICAO type, year, or owner/operator, the snapshot with the newer gzip-header timestamp wins;
- Wiedehopf long descriptions are retained when available, otherwise ADSBX manufacturer + model fill the readsb description field;
- military is retained when either source marks the aircraft military;
- Wiedehopf's `interesting` flag is retained;
- output uses readsb's native format: `ICAO;registration;icaotype;flags;description;year;owner/operator;`.

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

Generated database:

```text
/var/lib/readsb-aircraft-db/aircraft.csv.gz
```

Audit artifacts:

```text
/var/lib/readsb-aircraft-db/last-report.json
/var/lib/readsb-aircraft-db/last-conflicts.csv.gz
/var/lib/readsb-aircraft-db/backups/
```

## Validation baseline

Tested against the supplied August 24 / August 29, 2026 snapshots:

- output rows: 616,480
- registrations: 614,893
- descriptions: 498,948
- PIA records with registrations: 50,423 / 50,423
- LADD records with registrations: 30,315 / 30,315
- malformed readsb output rows: 0
- logged source conflicts/flag changes: 14,637

# readsb-aircraft-db

Builds freshness-aware aircraft databases for [readsb](https://github.com/wiedehopf/readsb) and VDLm2 Monitor by merging Wiedehopf's readsb/tar1090 aircraft CSV with ADS-B Exchange's `basic-ac-db.json.gz`.

Both output projections are produced from one shared reconciliation pass.

## Merge policy

- Union every valid ICAO address known to either source.
- A blank value never replaces a known value.
- PIA/LADD flags **never** suppress registration or other aircraft metadata.
- When both sources contain different nonblank values for registration, ICAO type, year, or owner/operator, the snapshot with the newer gzip-header timestamp wins.
- Wiedehopf long descriptions are retained when available; otherwise ADSBx manufacturer + model fill the readsb description field.
- Military status is retained when either source marks the aircraft military.
- Wiedehopf's `interesting` flag is retained.

## Outputs

### readsb

The readsb projection uses readsb's native semicolon-delimited format:

```text
ICAO;registration;icaotype;flags;description;year;owner/operator;
```

Installed path:

```text
/var/lib/readsb-aircraft-db/aircraft.csv.gz
```

### VDLm2 Monitor

The VDLm2 projection is UTF-8 newline-delimited JSON, sorted by ICAO. Every record has exactly these fields:

```text
icao, reg, icaotype, year, manufacturer, model, ownop,
faa_pia, faa_ladd, short_type, mil
```

Installed path:

```text
/var/lib/readsb-aircraft-db/vdlm2-aircraft.json
```

Unknown strings and years are JSON `null`; flags are booleans and valid years are integers.

ADSBx is the only source for manufacturer, model, and short type. Those three fields are conservatively suppressed when a newer Wiedehopf snapshot wins a conflicting nonblank registration or ICAO type, because the ADSBx descriptors may belong to the displaced airframe identity. Wiedehopf descriptions are never heuristically split.

## Deployment

The repository is public. No GitHub account, SSH key, personal access token, or GitHub CLI authentication is required to install or update it.

### Requirements

Runtime requirements:

- Linux with Bash
- Python 3
- `gzip`
- `sha256sum`
- either `curl` or `wget`
- `flock` is recommended to prevent overlapping updater runs
- a cron daemon if automatic daily updates are desired

On Debian, Ubuntu, or Raspberry Pi OS, the usual packages are:

```bash
sudo apt update
sudo apt install -y git python3 curl gzip coreutils util-linux cron
```

### 1. Clone the public repository

```bash
git clone https://github.com/r4streando/readsb-aircraft-db.git
cd readsb-aircraft-db
```

### 2. Install the updater

```bash
sudo bash install.sh
```

The installer copies:

```text
/usr/local/lib/readsb-aircraft-db/readsb-db-merge.py
/usr/local/sbin/update-readsb-aircraft-db
/etc/cron.d/readsb-aircraft-db
```

and creates the state directory:

```text
/var/lib/readsb-aircraft-db/
```

### 3. Build the database for the first time

```bash
sudo /usr/local/sbin/update-readsb-aircraft-db
```

This downloads fresh copies of both upstream source databases, validates them, performs the merge, validates both generated projections, and atomically installs the results.

No GitHub credentials or ADS-B Exchange API key are required.

### 4. Point readsb at the generated database

Add these options to the readsb decoder command line:

```text
--db-file /var/lib/readsb-aircraft-db/aircraft.csv.gz --db-file-lt
```

On common Debian/Raspberry Pi OS readsb installations, decoder options are configured in:

```text
/etc/default/readsb
```

Append the two flags to the existing `DECODER_OPTIONS` value rather than replacing any options already present.

Restart readsb once after changing its command-line configuration:

```bash
sudo systemctl restart readsb
```

Current readsb versions monitor the database file for mtime changes, so subsequent database refreshes do **not** require a readsb restart.

### 5. Verify the installation

Check that the generated files and audit report exist:

```bash
ls -lh \
  /var/lib/readsb-aircraft-db/aircraft.csv.gz \
  /var/lib/readsb-aircraft-db/vdlm2-aircraft.json \
  /var/lib/readsb-aircraft-db/last-report.json \
  /var/lib/readsb-aircraft-db/last-conflicts.csv.gz

gzip -t /var/lib/readsb-aircraft-db/aircraft.csv.gz
```

To confirm that readsb is actually running with the database options:

```bash
tr '\0' ' ' </proc/$(pidof readsb)/cmdline
echo
```

You should see:

```text
--db-file /var/lib/readsb-aircraft-db/aircraft.csv.gz --db-file-lt
```

## Automatic database updates

The installer places `/etc/cron.d/readsb-aircraft-db`, which runs the updater every day at **03:17 local time**:

```text
17 03 * * * root READSB_DB_SYSLOG=1 /usr/local/sbin/update-readsb-aircraft-db
```

Each run downloads both upstream databases fresh. It does **not** depend on the local Git checkout and does not run `git pull`.

The updater:

- validates both downloaded gzip files;
- reconciles the two source snapshots;
- validates both generated projections before publication;
- writes an audit report and compressed conflict report;
- keeps rolling backups;
- atomically replaces each installed projection independently;
- preserves an existing file's mtime when its content is unchanged;
- does not restart readsb or VDLm2 Monitor.

With `READSB_DB_SYSLOG=1`, cron output is sent through `logger` using the tag `readsb-aircraft-db`. On systemd systems it can typically be inspected with:

```bash
journalctl -t readsb-aircraft-db
```

## Updating the installed code

Daily database refreshes do not require updating the repository checkout. Pull the repository only when you want newer merger/updater code.

From the checkout created during deployment:

```bash
cd readsb-aircraft-db
git pull --ff-only
sudo bash install.sh
sudo /usr/local/sbin/update-readsb-aircraft-db
```

Because the repository is public, `git pull` over HTTPS requires no GitHub authentication.

## Upstream data sources

The updater currently downloads:

- Wiedehopf tar1090/readsb database: `https://raw.githubusercontent.com/wiedehopf/tar1090-db/csv/aircraft.csv.gz`
- ADS-B Exchange basic aircraft database: `https://downloads.adsbexchange.com/downloads/basic-ac-db.json.gz`

Both URLs can be overridden with environment variables.

## State, reports, and backups

Generated databases:

```text
/var/lib/readsb-aircraft-db/aircraft.csv.gz
/var/lib/readsb-aircraft-db/vdlm2-aircraft.json
```

Audit artifacts:

```text
/var/lib/readsb-aircraft-db/last-report.json
/var/lib/readsb-aircraft-db/last-conflicts.csv.gz
/var/lib/readsb-aircraft-db/backups/
```

The updater stages changed files beside their destinations and atomically renames them into place only after validation succeeds.

Content-change detection is independent for the two projections:

- readsb uses SHA-256 of the **uncompressed** CSV content;
- VDLm2 uses SHA-256 of the JSON file.

Backups are independently scoped as well.

VDLm2 Monitor must be restarted, or implement its own hot-reload mechanism, before it will load a changed VDLm2 database.

## Configuration overrides

The updater can be customized with environment variables, including:

```text
STATE_DIR
DEST
BACKUP_DIR
VDLM2_DEST
VDLM2_BACKUP_DIR
WIEDEHOPF_URL
ADSBX_URL
MIN_RECORDS
MAX_INPUT_ERRORS
KEEP_BACKUPS
VDLM2_KEEP_BACKUPS
LOCKFILE
MERGER
READSB_DB_SYSLOG
```

Defaults are defined near the top of `update-readsb-aircraft-db`.

## Tests

The test suite uses generated local fixtures and temporary directories only:

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

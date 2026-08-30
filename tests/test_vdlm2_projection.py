import gzip
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MERGER = ROOT / 'readsb-db-merge.py'
UPDATER = ROOT / 'update-readsb-aircraft-db'

SPEC = importlib.util.spec_from_file_location('merger', MERGER)
MERGER_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MERGER_MODULE
SPEC.loader.exec_module(MERGER_MODULE)

WIEDEHOPF = """\
000002;N2;B738;1100;Boeing 737-800;2010;Shared Air;
000003;;C172;0101;Cessna 172;not-a-year;;
000004;NEWREG;A320;1010;New W desc;2020;New owner;
"""

ADSBX_RECORDS = [
    {'icao': '000001', 'reg': 'N1', 'icaotype': 'a21n', 'year': '2022',
     'manufacturer': 'Airbus', 'model': 'A321neo', 'ownop': 'Only ADSB',
     'faa_pia': True, 'faa_ladd': True, 'short_type': 'l2j', 'mil': False},
    {'icao': '000002', 'reg': 'N2', 'icaotype': 'B738', 'year': '2010',
     'manufacturer': 'Boeing', 'model': '737-800', 'ownop': 'Shared Air',
     'faa_pia': True, 'faa_ladd': True, 'short_type': 'L2J', 'mil': False},
    {'icao': '000004', 'reg': 'OLDREG', 'icaotype': 'B738', 'year': '1999',
     'manufacturer': 'Old Maker', 'model': 'Old Model', 'ownop': 'Old owner',
     'faa_pia': False, 'faa_ladd': False, 'short_type': 'L2J', 'mil': False},
]

# Frozen decompressed output from the pre-refactor merger for these fixtures.
FROZEN_READSB = """\
000001;N1;A21N;0011;Airbus A321neo;2022;Only ADSB;
000002;N2;B738;11;Boeing 737-800;2010;Shared Air;
000003;;C172;0101;Cessna 172;not-a-year;;
000004;NEWREG;A320;1010;New W desc;2020;New owner;
"""


def write_gzip(path, text, mtime):
    with path.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=mtime) as zipped:
            zipped.write(text.encode())


def make_sources(directory):
    wiedehopf = directory / 'aircraft.csv.gz'
    adsbx = directory / 'basic-ac-db.json.gz'
    write_gzip(wiedehopf, WIEDEHOPF, 200)
    adsbx_text = ''.join(json.dumps(record) + '\n' for record in ADSBX_RECORDS)
    write_gzip(adsbx, adsbx_text, 100)
    return wiedehopf, adsbx


def run_merger(directory):
    wiedehopf, adsbx = make_sources(directory)
    readsb = directory / 'aircraft.out.csv.gz'
    vdlm2 = directory / 'vdlm2.out.json'
    subprocess.run([
        'python3', str(MERGER), '--wiedehopf', str(wiedehopf),
        '--adsbx', str(adsbx), '--output', str(readsb),
        '--vdlm2-output', str(vdlm2), '--min-records', '1'
    ], check=True, capture_output=True, text=True)
    with gzip.open(readsb, 'rt') as source:
        readsb_text = source.read()
    records = [json.loads(line) for line in vdlm2.read_text().splitlines()]
    return readsb_text, records


class ProjectionTests(unittest.TestCase):
    def test_selection_flags_and_cleaning_regressions(self):
        self.assertEqual(MERGER_MODULE.choose('', 'known', 'wiedehopf'),
                         ('known', 'adsbx', False))
        self.assertEqual(MERGER_MODULE.choose('old', 'new', 'adsbx'),
                         ('new', 'adsbx', True))
        self.assertEqual(MERGER_MODULE.parse_flags('1011'), (True, False, True, True))
        self.assertEqual(MERGER_MODULE.fmt_flags(True, False, False, False), '10')
        self.assertEqual(MERGER_MODULE.clean(' a;\x00\r\n b '), 'a, b')

    def test_parsers_repair_json_count_duplicates_and_enforce_error_threshold(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            wiedehopf = root / 'w.csv'
            wiedehopf.write_text(
                'ABCDEF;FIRST;A320;00;Desc;2001;Owner;\n'
                'ABCDEF;LAST;B738;00;Desc;2002;Owner;\n'
                'bad row\n')
            db, stats, _ = MERGER_MODULE.parse_wiedehopf(wiedehopf, 1)
            self.assertEqual(db['ABCDEF']['reg'], 'LAST')
            self.assertEqual(stats['duplicates'], 1)
            self.assertEqual(stats['malformed_rows'], 1)
            with self.assertRaises(RuntimeError):
                MERGER_MODULE.parse_wiedehopf(wiedehopf, 0)

            adsbx = root / 'a.json'
            adsbx.write_text(
                '{"icao":"ABCDEF","reg":"FIRST"}\n'
                '{"icao":"ABCDEF","reg":"LAST"}\n'
                '{"icao":"123456","ownop":"escaped \\\\"quote\\\\""}\n')
            adb, astats, _ = MERGER_MODULE.parse_adsbx(adsbx, 0)
            self.assertEqual(adb['ABCDEF']['reg'], 'LAST')
            self.assertEqual(astats['duplicates'], 1)
            self.assertEqual(astats['repaired_json_rows'], 1)

    def test_readsb_decompressed_output_is_frozen_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as name:
            readsb, _ = run_merger(Path(name))
        self.assertEqual(readsb.encode(), FROZEN_READSB.encode())

    def test_vdlm2_union_types_nulls_and_identity_suppression(self):
        with tempfile.TemporaryDirectory() as name:
            _, records = run_merger(Path(name))
        by_icao = {record['icao']: record for record in records}
        self.assertEqual(list(by_icao), ['000001', '000002', '000003', '000004'])
        self.assertEqual(set(by_icao), {'000001', '000002', '000003', '000004'})

        adsbx_only = by_icao['000001']
        self.assertEqual(adsbx_only['icaotype'], 'A21N')
        self.assertEqual(adsbx_only['short_type'], 'L2J')
        self.assertEqual(adsbx_only['year'], 2022)
        self.assertIs(type(adsbx_only['faa_pia']), bool)

        shared = by_icao['000002']
        self.assertEqual(shared['manufacturer'], 'Boeing')
        self.assertEqual(shared['model'], '737-800')
        self.assertTrue(shared['mil'])
        self.assertFalse(shared['faa_pia'])  # newer Wiedehopf snapshot

        wiedehopf_only = by_icao['000003']
        self.assertIsNone(wiedehopf_only['reg'])
        self.assertIsNone(wiedehopf_only['year'])
        self.assertIsNone(wiedehopf_only['manufacturer'])
        self.assertIsNone(wiedehopf_only['model'])
        self.assertIsNone(wiedehopf_only['short_type'])
        self.assertTrue(wiedehopf_only['faa_ladd'])

        conflicting = by_icao['000004']
        self.assertEqual(conflicting['reg'], 'NEWREG')
        self.assertEqual(conflicting['icaotype'], 'A320')
        self.assertIsNone(conflicting['manufacturer'])
        self.assertIsNone(conflicting['model'])
        self.assertIsNone(conflicting['short_type'])
        self.assertEqual(conflicting['year'], 2020)

        expected_keys = ['icao', 'reg', 'icaotype', 'year', 'manufacturer', 'model',
                         'ownop', 'faa_pia', 'faa_ladd', 'short_type', 'mil']
        for record in records:
            self.assertEqual(list(record), expected_keys)


class UpdaterIntegrationTests(unittest.TestCase):
    def updater_env(self, root, merger=MERGER):
        source_dir = root / 'sources'
        source_dir.mkdir(exist_ok=True)
        wiedehopf, adsbx = make_sources(source_dir)
        state = root / 'state'
        return {
            **os.environ,
            'MERGER': str(merger), 'STATE_DIR': str(state),
            'DEST': str(state / 'aircraft.csv.gz'),
            'VDLM2_DEST': str(state / 'vdlm2-aircraft.json'),
            'BACKUP_DIR': str(state / 'backups/readsb'),
            'VDLM2_BACKUP_DIR': str(state / 'backups/vdlm2'),
            'LOCKFILE': str(root / 'updater.lock'),
            'WIEDEHOPF_URL': wiedehopf.as_uri(), 'ADSBX_URL': adsbx.as_uri(),
            'MIN_RECORDS': '1', 'MAX_INPUT_ERRORS': '0',
        }

    def test_updater_installs_both_then_preserves_independent_mtimes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            env = self.updater_env(root)
            subprocess.run(['bash', str(UPDATER)], env=env, check=True, capture_output=True, text=True)
            readsb = Path(env['DEST'])
            vdlm2 = Path(env['VDLM2_DEST'])
            self.assertTrue(readsb.is_file())
            self.assertTrue(vdlm2.is_file())
            first_mtimes = (readsb.stat().st_mtime_ns, vdlm2.stat().st_mtime_ns)
            time.sleep(0.01)
            result = subprocess.run(['bash', str(UPDATER)], env=env, check=True,
                                    capture_output=True, text=True)
            self.assertEqual(first_mtimes,
                             (readsb.stat().st_mtime_ns, vdlm2.stat().st_mtime_ns))
            self.assertIn('both projections unchanged', result.stdout)

            # Damage only VDLm2. A subsequent run republishes it without touching readsb.
            vdlm2.write_text('stale\n')
            readsb_mtime = readsb.stat().st_mtime_ns
            result = subprocess.run(['bash', str(UPDATER)], env=env, check=True,
                                    capture_output=True, text=True)
            self.assertEqual(readsb_mtime, readsb.stat().st_mtime_ns)
            self.assertIn('readsb projection unchanged', result.stdout)
            self.assertNotEqual(vdlm2.read_text(), 'stale\n')

    def test_invalid_either_candidate_leaves_both_outputs_untouched(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fake = root / 'invalid_merger.py'
            fake.write_text("""\
import argparse, gzip, os
from pathlib import Path
p=argparse.ArgumentParser()
for name in ('wiedehopf','adsbx','output','vdlm2-output','report','conflicts'):
    p.add_argument('--'+name)
p.add_argument('--min-records'); p.add_argument('--max-input-errors')
a=p.parse_args()
if os.environ['INVALID_TARGET'] == 'readsb':
    Path(a.output).write_text('not gzip')
else:
    with gzip.open(a.output, 'wt') as f: f.write('000001;;;;;;;\\n')
valid='{"icao":"000001","reg":null,"icaotype":null,"year":null,"manufacturer":null,"model":null,"ownop":null,"faa_pia":false,"faa_ladd":false,"short_type":null,"mil":false}\\n'
Path(a.vdlm2_output).write_text('{bad json}\\n' if os.environ['INVALID_TARGET'] == 'vdlm2' else valid)
Path(a.report).write_text('{}\\n')
with gzip.open(a.conflicts, 'wt') as f: f.write('header\\n')
""")
            env = self.updater_env(root, fake)
            state = Path(env['STATE_DIR'])
            state.mkdir(parents=True)
            readsb = Path(env['DEST'])
            vdlm2 = Path(env['VDLM2_DEST'])
            for invalid_target in ('readsb', 'vdlm2'):
                with self.subTest(invalid_target=invalid_target):
                    readsb.write_bytes(b'original-readsb')
                    vdlm2.write_bytes(b'original-vdlm2')
                    env['INVALID_TARGET'] = invalid_target
                    result = subprocess.run(['bash', str(UPDATER)], env=env,
                                            capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(readsb.read_bytes(), b'original-readsb')
                    self.assertEqual(vdlm2.read_bytes(), b'original-vdlm2')


if __name__ == '__main__':
    unittest.main()

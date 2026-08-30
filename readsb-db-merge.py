#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, datetime as dt, gzip, hashlib, io, json, os, re, struct, sys, tempfile, time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ICAO_RE = re.compile(r'^[0-9A-F]{6}$')

@dataclass(frozen=True)
class Aircraft:
    icao: str
    reg: str
    icaotype: str
    year: str
    ownop: str
    manufacturer: str
    model: str
    short_type: str
    description: str
    mil: bool
    interesting: bool
    faa_pia: bool
    faa_ladd: bool

def clean(v):
    if v is None: return ''
    s = str(v).strip().replace(';', ',').replace('\x00', '').replace('\r', ' ').replace('\n', ' ')
    return ' '.join(s.split())

def gz_mtime(p: Path) -> int:
    try:
        h = p.open('rb').read(10)
        return struct.unpack('<I', h[4:8])[0] if len(h) >= 10 and h[:3] == b'\x1f\x8b\x08' else 0
    except OSError:
        return 0

def open_text(p: Path):
    with p.open('rb') as f: magic = f.read(2)
    return gzip.open(p, 'rt', encoding='utf-8', errors='replace', newline='') if magic == b'\x1f\x8b' else p.open('rt', encoding='utf-8', errors='replace', newline='')

def sha256(p: Path):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()

def parse_flags(s):
    s = clean(s) + '0000'
    return tuple(s[i] == '1' for i in range(4))

def fmt_flags(mil, interesting, pia, ladd):
    s = ''.join('1' if x else '0' for x in (mil, interesting, pia, ladd))
    return s[:2] if s[2:] == '00' else s

def parse_wiedehopf(p: Path, max_errors: int):
    db, stats, errors = {}, Counter(), []
    with open_text(p) as f:
        for n, line in enumerate(f, 1):
            stats['physical_lines'] += 1
            a = line.rstrip('\r\n').split(';')
            if len(a) != 8:
                stats['malformed_rows'] += 1
                if len(errors) < 20: errors.append({'line': n, 'error': f'expected 8 fields, got {len(a)}'})
                continue
            icao = a[0].strip().upper()
            if not ICAO_RE.fullmatch(icao):
                stats['bad_icao'] += 1
                continue
            if icao in db: stats['duplicates'] += 1
            db[icao] = {'reg': clean(a[1]), 'type': clean(a[2]).upper(), 'flags': clean(a[3]), 'desc': clean(a[4]), 'year': clean(a[5]), 'ownop': clean(a[6])}
            stats['valid_rows'] += 1
    if stats['malformed_rows'] + stats['bad_icao'] > max_errors:
        raise RuntimeError('too many Wiedehopf parse errors')
    return db, stats, errors

def repair_json(line):
    fixed = line.replace(r'\\"', r'\"')
    if fixed == line: return None
    try: return json.loads(fixed)
    except json.JSONDecodeError: return None

def parse_adsbx(p: Path, max_errors: int):
    db, stats, errors = {}, Counter(), []
    with open_text(p) as f:
        for n, line in enumerate(f, 1):
            stats['physical_lines'] += 1
            try: a = json.loads(line)
            except json.JSONDecodeError as e:
                a = repair_json(line)
                if a is None:
                    stats['malformed_json'] += 1
                    if len(errors) < 20: errors.append({'line': n, 'error': str(e)})
                    continue
                stats['repaired_json_rows'] += 1
            icao = clean(a.get('icao')).upper()
            if not ICAO_RE.fullmatch(icao):
                stats['bad_icao'] += 1
                continue
            if icao in db: stats['duplicates'] += 1
            db[icao] = a
            stats['valid_rows'] += 1
    if stats['malformed_json'] + stats['bad_icao'] > max_errors:
        raise RuntimeError('too many ADSBX parse errors')
    return db, stats, errors

def choose(w, a, fresher):
    w, a = clean(w), clean(a)
    if w and a:
        if w == a: return w, 'both', False
        return (a, 'adsbx', True) if fresher == 'adsbx' else (w, 'wiedehopf', True)
    if a: return a, 'adsbx', False
    if w: return w, 'wiedehopf', False
    return '', 'none', False

def ads_desc(a):
    if not a: return ''
    vals = []
    for k in ('manufacturer', 'model'):
        v = clean(a.get(k))
        if v and v.casefold() not in [x.casefold() for x in vals]: vals.append(v)
    return ' '.join(vals)

def atomic_gzip_writer(target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.tmp', dir=target.parent)
    raw = os.fdopen(fd, 'wb')
    gz = gzip.GzipFile(filename='', mode='wb', fileobj=raw, compresslevel=9, mtime=int(time.time()))
    return io.TextIOWrapper(gz, encoding='utf-8', newline='\n'), Path(name)

def atomic_text_writer(target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.tmp', dir=target.parent)
    return os.fdopen(fd, 'w', encoding='utf-8', newline='\n'), Path(name)

def finish(fh, tmp, target):
    fh.flush(); fh.close(); os.chmod(tmp, 0o644); os.replace(tmp, target)

def reconcile(wdb, adb, fresher, conflicts, stats):
    for icao in sorted(set(wdb) | set(adb)):
        w, a = wdb.get(icao), adb.get(icao)
        wr, wt, wy, wo = (w.get('reg',''), w.get('type',''), w.get('year',''), w.get('ownop','')) if w else ('','','','')
        ar, at, ay, ao = (a.get('reg',''), a.get('icaotype',''), a.get('year',''), a.get('ownop','')) if a else ('','','','')
        reg, rsrc, rc = choose(wr, ar, fresher)
        typ, tsrc, tc = choose(wt, at, fresher); typ = typ.upper()
        year, ysrc, yc = choose(wy, ay, fresher)
        own, osrc, oc = choose(wo, ao, fresher)
        for field, c, wv, av, val, src in (('registration',rc,wr,ar,reg,rsrc),('icaotype',tc,wt,at,typ,tsrc),('year',yc,wy,ay,year,ysrc),('owner',oc,wo,ao,own,osrc)):
            if c: conflicts.append([icao, field, clean(wv), clean(av), val, src, 'newer_nonblank_source'])

        wd = clean(w.get('desc')) if w else ''
        ad = ads_desc(a)
        desc = wd or ad
        if not wd and ad: stats['description_filled_from_adsbx'] += 1

        if w: wmil, wint, wpia, wladd = parse_flags(w.get('flags',''))
        else: wmil=wint=wpia=wladd=False
        if a: amil, apia, aladd = bool(a.get('mil')), bool(a.get('faa_pia')), bool(a.get('faa_ladd'))
        else: amil=apia=aladd=False

        mil = wmil or amil
        interesting = wint
        if w and a:
            pia, ladd = (apia, aladd) if fresher == 'adsbx' else (wpia, wladd)
            if wpia != apia: conflicts.append([icao,'PIA',int(wpia),int(apia),int(pia),fresher,'newer_snapshot'])
            if wladd != aladd: conflicts.append([icao,'LADD',int(wladd),int(aladd),int(ladd),fresher,'newer_snapshot'])
            if wmil != amil: conflicts.append([icao,'military',int(wmil),int(amil),int(mil),'either_true','logical_OR'])
        elif a: pia, ladd = apia, aladd
        else: pia, ladd = wpia, wladd

        manufacturer = clean(a.get('manufacturer')) if a else ''
        model = clean(a.get('model')) if a else ''
        short_type = clean(a.get('short_type')).upper() if a else ''
        # ADSBx-only descriptors may belong to a stale/displaced airframe. Suppress
        # them when newer Wiedehopf identity data wins a nonblank reg/type conflict.
        displaced_adsbx_identity = fresher == 'wiedehopf' and (rc or tc)
        if displaced_adsbx_identity:
            manufacturer = model = short_type = ''
            stats['adsbx_descriptors_suppressed_identity_conflict'] += 1

        yield Aircraft(icao, reg, typ, year, own, manufacturer, model, short_type,
                       desc, mil, interesting, pia, ladd)

def render_readsb(ac: Aircraft):
    flags = fmt_flags(ac.mil, ac.interesting, ac.faa_pia, ac.faa_ladd)
    fields = [clean(x) for x in (ac.icao, ac.reg, ac.icaotype, flags,
                                  ac.description, ac.year, ac.ownop, '')]
    line = ';'.join(fields) + '\n'
    if line.count(';') != 7 or not line.rstrip('\n').endswith(';'):
        raise AssertionError(f'bad output structure for {ac.icao}')
    return line

def nullable(s):
    return s or None

def json_year(s):
    return int(s) if re.fullmatch(r'[0-9]+', s) else None

def render_vdlm2(ac: Aircraft):
    record = {
        'icao': ac.icao, 'reg': nullable(ac.reg),
        'icaotype': nullable(ac.icaotype), 'year': json_year(ac.year),
        'manufacturer': nullable(ac.manufacturer), 'model': nullable(ac.model),
        'ownop': nullable(ac.ownop), 'faa_pia': ac.faa_pia,
        'faa_ladd': ac.faa_ladd, 'short_type': nullable(ac.short_type),
        'mil': ac.mil,
    }
    return json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--wiedehopf', required=True, type=Path)
    ap.add_argument('--adsbx', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--vdlm2-output', type=Path)
    ap.add_argument('--report', type=Path)
    ap.add_argument('--conflicts', type=Path)
    ap.add_argument('--min-records', type=int, default=500000)
    ap.add_argument('--max-input-errors', type=int, default=100)
    args = ap.parse_args()

    wdb, ws, we = parse_wiedehopf(args.wiedehopf, args.max_input_errors)
    adb, aas, ae = parse_adsbx(args.adsbx, args.max_input_errors)
    if len(wdb) < args.min_records or len(adb) < args.min_records:
        raise RuntimeError(f'input too small: wiedehopf={len(wdb)} adsbx={len(adb)}')

    wm, am = gz_mtime(args.wiedehopf), gz_mtime(args.adsbx)
    if wm and am and wm != am: fresher, basis = ('adsbx', 'gzip_mtime') if am > wm else ('wiedehopf', 'gzip_mtime')
    elif am and not wm: fresher, basis = 'adsbx', 'gzip_mtime_only_adsbx'
    elif wm and not am: fresher, basis = 'wiedehopf', 'gzip_mtime_only_wiedehopf'
    else: fresher, basis = 'adsbx', 'tie_or_missing_mtime'

    all_icaos = sorted(set(wdb) | set(adb))
    coverage, stats, conflicts = Counter(), Counter(), []
    out, tmp = atomic_gzip_writer(args.output)
    vout = vtmp = None
    if args.vdlm2_output:
        vout, vtmp = atomic_text_writer(args.vdlm2_output)
    try:
        for ac in reconcile(wdb, adb, fresher, conflicts, stats):
            out.write(render_readsb(ac))
            if vout: vout.write(render_vdlm2(ac))

            if ac.reg: coverage['registration'] += 1
            if ac.icaotype: coverage['icaotype'] += 1
            if ac.description: coverage['description'] += 1
            if ac.year: coverage['year'] += 1
            if ac.ownop: coverage['owner_operator'] += 1
            if ac.faa_pia:
                coverage['PIA'] += 1
                if ac.reg: stats['pia_with_registration'] += 1
            if ac.faa_ladd:
                coverage['LADD'] += 1
                if ac.reg: stats['ladd_with_registration'] += 1
            a, w = adb.get(ac.icao), wdb.get(ac.icao)
            if a and bool(a.get('faa_pia')) and clean(a.get('reg')) and not clean(w.get('reg') if w else ''):
                stats['pia_registrations_restored_from_adsbx'] += 1
        finish(out, tmp, args.output)
        if vout: finish(vout, vtmp, args.vdlm2_output)
    except Exception:
        try:
            out.close()
            if vout: vout.close()
        finally:
            tmp.unlink(missing_ok=True)
            if vtmp: vtmp.unlink(missing_ok=True)
        raise

    if args.conflicts:
        fh, ctmp = atomic_gzip_writer(args.conflicts)
        cw = csv.writer(fh, lineterminator='\n')
        cw.writerow(['icao','field','wiedehopf','adsbx','chosen','chosen_source','rule'])
        cw.writerows(conflicts)
        finish(fh, ctmp, args.conflicts)

    report = {
        'generated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00','Z'),
        'policy': {'fresher_source':fresher,'freshness_basis':basis,'blank_never_overwrites_known':True,'pia_ladd_never_blank_metadata':True,'military':'logical OR','interesting':'Wiedehopf retained','adsbx_descriptors':'suppressed when newer Wiedehopf wins registration or ICAO-type conflict'},
        'sources': {
            'wiedehopf': {'gzip_mtime':wm,'sha256':sha256(args.wiedehopf),'parse':dict(ws),'sample_errors':we},
            'adsbx': {'gzip_mtime':am,'sha256':sha256(args.adsbx),'parse':dict(aas),'sample_errors':ae}},
        'sets': {'wiedehopf':len(wdb),'adsbx':len(adb),'shared':len(set(wdb)&set(adb)),'wiedehopf_only':len(set(wdb)-set(adb)),'adsbx_only':len(set(adb)-set(wdb)),'output':len(all_icaos)},
        'coverage': dict(coverage), 'merge_stats':dict(stats), 'conflicts':len(conflicts),
        'output': {'path':str(args.output),'bytes':args.output.stat().st_size,'sha256':sha256(args.output)} }
    if args.vdlm2_output:
        report['vdlm2_output'] = {'path':str(args.vdlm2_output),'bytes':args.vdlm2_output.stat().st_size,'sha256':sha256(args.vdlm2_output)}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        t = args.report.with_name('.'+args.report.name+'.tmp')
        t.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8'); os.chmod(t,0o644); os.replace(t,args.report)

    print(f"merged {len(all_icaos):,} ICAOs; fresher={fresher}; reg={coverage['registration']:,}; desc={coverage['description']:,}; PIA-with-reg={stats['pia_with_registration']:,}; LADD-with-reg={stats['ladd_with_registration']:,}; conflicts={len(conflicts):,}")

if __name__ == '__main__':
    try: main()
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        raise

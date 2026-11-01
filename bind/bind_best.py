"""
bind_best.py - run the three binders (auto = bind_to_106 bone-heat, donor = bind_donor --mode donor, hybrid = bind_donor --mode hybrid) on one GLB,
measure each with metrics2.py (new gate incl. the relative-to-donor-control check) and keep the best passing one.

  python C:\\Users\\dom12\\Documents\\agent_tests\\bind3\\bind_best.py <mesh.glb> <out.blend> [--stills DIR] [--work DIR]

Selection: HYBRID is the default; if its gate fails, DONOR is used when it passes; if neither passes the lowest score = max(p99_stretch/2.0, 0.5/p99_compression_ratio) is kept (add --with-auto to also run bone-heat as a candidate)
and the result is flagged GATE_FAIL.  The donor control (the picked donor, T-posed, used as its own target) is built once per donor and cached in bind3\\controls.
Every Blender run is killed after 290 s.
"""
import os, sys, json, shutil, subprocess, time
HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
BIND2 = r'C:\Users\dom12\Documents\agent_tests\bind2\bind_to_106.py'
def run(script, args, log, tmo=290):
    cmd = [BLENDER, '--background', '--python', script, '--'] + args
    t = time.time()
    with open(log, 'w') as fh:
        try: rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, timeout=tmo).returncode
        except subprocess.TimeoutExpired: rc = 'TIMEOUT'
    print('  [%s] %s rc=%s %.0fs' % (os.path.basename(script), os.path.basename(log), rc, time.time() - t), flush=True)
    return rc
def main():
    a = sys.argv[1:]; pos = []; opt = {}; i = 0
    while i < len(a):
        if a[i] == '--with-auto': i += 1
        elif a[i].startswith('--'): opt[a[i]] = a[i + 1]; i += 2
        else: pos.append(a[i]); i += 1
    mesh, out = [os.path.abspath(p) for p in pos]
    stem = os.path.splitext(os.path.basename(mesh))[0]
    work = os.path.abspath(opt.get('--work', os.path.join(HERE, 'best_work', stem))); os.makedirs(work, exist_ok=True)
    ctl_dir = os.path.join(HERE, 'controls'); os.makedirs(ctl_dir, exist_ok=True)
    bd = os.path.join(HERE, 'bind_donor.py'); mt = os.path.join(HERE, 'metrics2.py')
    B = {k: os.path.join(work, k + '.blend') for k in ('auto', 'donor', 'hybrid')}
    print('binding', mesh, flush=True)
    run(bd, [mesh, B['hybrid'], '--mode', 'hybrid'], os.path.join(work, 'hybrid.log'))
    run(bd, [mesh, B['donor'], '--mode', 'donor', '--donor', json.load(open(os.path.splitext(B['hybrid'])[0] + '.report.json'))['donor']], os.path.join(work, 'donor.log'))
    if '--with-auto' in a: run(BIND2, [mesh, B['auto']], os.path.join(work, 'auto.log'))
    donor = json.load(open(os.path.splitext(B['hybrid'])[0] + '.report.json'))['donor']
    cglb = os.path.join(ctl_dir, 'donor_%s_tpose.glb' % donor); cbl = os.path.join(ctl_dir, 'control_%s.blend' % donor); cj = os.path.join(ctl_dir, 'control_%s.metrics.json' % donor)
    if not os.path.exists(cj):
        if not os.path.exists(cglb): run(os.path.join(HERE, 'mk_donor_tpose.py'), [donor, cglb], os.path.join(ctl_dir, 'mk_%s.log' % donor))
        run(bd, [cglb, cbl, '--mode', 'donor', '--donor', donor, '--smooth-passes', '2'], os.path.join(ctl_dir, 'control_%s.log' % donor))
        run(mt, [cbl, cj], os.path.join(ctl_dir, 'control_%s.metrics.log' % donor))
    res = {}
    for k in B:
        if k == 'auto' and '--with-auto' not in a: continue
        if not os.path.exists(B[k]): res[k] = {'error': 'no blend produced'}; continue
        mj = os.path.join(work, k + '.metrics.json')
        run(mt, [B[k], mj, '--control', cj], os.path.join(work, k + '.metrics.log'))
        if os.path.exists(mj): res[k] = json.load(open(mj))
        else: res[k] = {'error': 'metrics failed'}
    ok = {k: v for k, v in res.items() if 'score' in v}
    if not ok: print('NO BINDER PRODUCED A MEASURABLE RESULT'); sys.exit(1)
    passing = {k: v for k, v in ok.items() if v['gate_pass']}
    best = 'hybrid' if 'hybrid' in passing else ('donor' if 'donor' in passing else min(ok, key=lambda k: ok[k]['score']))      # default HYBRID, DONOR is the alternative
    shutil.copyfile(B[best], out)
    if '--stills' in opt: run(mt, [out, os.path.join(work, 'best.metrics.json'), '--control', cj, '--stills', os.path.abspath(opt['--stills'])], os.path.join(work, 'best.stills.log'))
    summ = {'mesh': mesh, 'donor': donor, 'best': best, 'gate': 'PASS' if passing else 'GATE_FAIL', 'out': out,
            'binders': {k: {'score': v['score'], 'p99_stretch': v['p99_stretch'], 'p99_compression_ratio': v['p99_compression_ratio'], 'gate_pass': v['gate_pass'], 'regions': v['violating_edges_by_region']} for k, v in ok.items()}}
    json.dump(summ, open(os.path.splitext(out)[0] + '.best.json', 'w'), indent=1)
    print(json.dumps(summ, indent=1)); print('BEST: %s (%s) -> %s' % (best, summ['gate'], out))
if __name__ == '__main__': main()

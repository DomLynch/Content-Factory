"""sweep.py - smoothing-pass sweep: python sweep.py <N> [<N>...]   (writes C:\\Users\\dom12\\Documents\\agent_tests\\bind3\\sweep\\...)"""
import os, sys, json, subprocess
from concurrent.futures import ThreadPoolExecutor
H = r'C:\Users\dom12\Documents\agent_tests\bind3'; BL = r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
MESH = {'male_muscular': 'giant', 'female_slim': 'giant', 'male_heavy_old': 'dwarf'}
SRC = r'C:\Users\dom12\Documents\agent_tests\mpfb'
W = os.path.join(H, 'sweep'); os.makedirs(W, exist_ok=True)
def run(script, args, log):
    with open(log, 'w') as fh:
        try: return subprocess.run([BL, '--background', '--python', os.path.join(H, script), '--'] + args, stdout=fh, stderr=subprocess.STDOUT, timeout=290).returncode
        except subprocess.TimeoutExpired: return 'TIMEOUT'
def job(a):
    kind, n, name, mode = a            # kind 'cand' or 'ctl'
    if kind == 'ctl':
        b = os.path.join(W, 'control_%s_%d.blend' % (name, n)); run('bind_donor.py', [os.path.join(H, 'controls', 'donor_%s_tpose.glb' % name), b, '--mode', 'donor', '--donor', name, '--smooth-passes', str(n)], b + '.bind.log')
    else:
        b = os.path.join(W, '%s_%s_%d.blend' % (name, mode, n)); run('bind_donor.py', [os.path.join(SRC, name + '.glb'), b, '--mode', mode, '--donor', MESH[name], '--smooth-passes', str(n)], b + '.bind.log')
    run('metrics2.py', [b, b + '.metrics.json'], b + '.metrics.log'); return b
def main():
    Ns = [int(x) for x in sys.argv[1:]]; jobs = []
    for n in Ns:
        for d in ('giant', 'dwarf'): jobs.append(('ctl', n, d, None))
        for name in MESH:
            for mode in ('hybrid', 'donor'): jobs.append(('cand', n, name, mode))
    with ThreadPoolExecutor(4) as ex: list(ex.map(job, jobs))
    for n in Ns:
        print('\n== smoothing passes', n)
        for name, d in MESH.items():
            c = json.load(open(os.path.join(W, 'control_%s_%d.blend.metrics.json' % (d, n)))); c2 = json.load(open(os.path.join(H, 'controls', 'control_%s.metrics.json' % d)))
            print(name, 'donor', d, 'control@%d p99s %.3f ratio %.3f | control@2 p99s %.3f ratio %.3f' % (n, c['p99_stretch'], c['p99_compression_ratio'], c2['p99_stretch'], c2['p99_compression_ratio']))
            for mode in ('hybrid', 'donor'):
                f = os.path.join(W, '%s_%s_%d.blend.metrics.json' % (name, mode, n))
                if not os.path.exists(f): print('  ', mode, 'FAILED'); continue
                r = json.load(open(f)); s, q = r['p99_stretch'], r['p99_compression_ratio']; w = r['weights']
                relN = s <= c['p99_stretch'] * 1.1 and q >= c['p99_compression_ratio'] * 0.9; rel2 = s <= c2['p99_stretch'] * 1.1 and q >= c2['p99_compression_ratio'] * 0.9
                absok = s <= 2.0 and q >= 0.5; wok = w['zero_weight_vertices'] == 0 and w['max_influences'] <= 4 and w['weight_sum_max_dev'] <= 1e-3
                g = r['violating_edges_by_region']
                print('   %-6s p99s %.3f ratio %.3f | rel vs ctl@%d %s | rel vs ctl@2 %s | abs %s weights %s | regions h/s/c/n/f %d/%d/%d/%d/%d | unweighted non-toe %d %s -> %s' % (mode, s, q, n, relN, rel2, absok, wok, g['hands'], g['shoulders'], g['crotch'], g['neck'], g['feet'], len(w['unweighted_bones_other_than_toe_end']), [x.replace('mixamorig:', '') for x in w['unweighted_bones_other_than_toe_end']], 'PASS' if (relN and absok and wok) else 'FAIL'))
main()

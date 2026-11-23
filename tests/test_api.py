"""Tests for the replay database API, against the database in credentials/config.py (a copy, never
the live one: section 4 deletes a replay and uploads it again).

  LD_LIBRARY_PATH=... python tests/test_api.py [old_server_url]

1. /api/v2/replays: random combinations of every filter, every page followed through its cursor,
   compared with the same filter worked out in Python over the whole table (exact rows, exact
   order), plus the counts.
2. /api/v2/players, /players/<id>, /players/<id>/replays, /characters/<c>/players, /filenames against the same
   reference.
3. The old /get_replays: answers byte-for-byte the same as the server's current code (run
   separately at old_server_url).
4. /upload: a replay that is not in the database is stored with its levels and the summaries
   move by exactly one; the same replay again changes nothing.
"""
import collections, datetime, gzip, json, os, random, sys, tarfile, time, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
os.chdir(os.path.join(os.path.dirname(__file__), '..', 'backend'))
import mariadb
import app as backend

backend.limiter.enabled = False
client = backend.app.test_client()
# the database the backend itself uses (credentials/config.py), so the checks read what it wrote
C = backend.Config
db = mariadb.connect(host=C.DB_HOST, port=getattr(C, 'DB_PORT', 3306), user=C.DB_USER, password=C.DB_PASSWORD,
                     database=C.DATABASE, autocommit=True)
cur = db.cursor()
failures = []
RUN = os.environ.get('RUN', '12345')   # which sections


def check(cond, what):
    if not cond:
        failures.append(what)
        print('  FAIL', what)


# ---- reference: the whole table in memory
cur.execute("SELECT filename, datetime_, upload_datetime_, p1, p1_toon, p2, p2_toon, recorder, winner, p1_steamid64, p2_steamid64, "
            "recorder_steamid64, p1_level, p2_level FROM replay_metadata")
COLS = ['filename', 'datetime_', 'upload_datetime_', 'p1', 'p1_toon', 'p2', 'p2_toon', 'recorder', 'winner', 'p1_steamid64',
        'p2_steamid64', 'recorder_steamid64', 'p1_level', 'p2_level']
ROWS = [dict(zip(COLS, r)) for r in cur.fetchall()]
cur.execute("SELECT steamid64, name FROM player_names")
NAMES = collections.defaultdict(set)
for sid, nm in cur.fetchall():
    NAMES[sid].add(nm)
print('reference rows', len(ROWS))


def ref_query(f):
    """The filters of /api/v2/replays, by brute force."""
    side = f.get('side', 'any')
    def named(q):
        q = q.lower()
        return {sid for sid, ns in NAMES.items() if any(q in n.lower() for n in ns)}
    name_ids = named(f['name']) if 'name' in f else None
    opp_ids = named(f['opponent_name']) if 'opponent_name' in f else None
    sided = any(k in f for k in ('player', 'character', 'name', 'opponent', 'opponent_character', 'opponent_name'))
    out = []
    for r in ROWS:
        ok_any = False
        for me in ((1, 2) if side == 'any' else (int(side),)) if sided else (None,):
            ok = True
            if me:
                other = 3 - me
                if 'player' in f and r['p%d_steamid64' % me] != f['player']: ok = False
                if 'character' in f and r['p%d_toon' % me] != f['character']: ok = False
                if name_ids is not None and r['p%d_steamid64' % me] not in name_ids: ok = False
                if 'opponent' in f and r['p%d_steamid64' % other] != f['opponent']: ok = False
                if 'opponent_character' in f and r['p%d_toon' % other] != f['opponent_character']: ok = False
                if opp_ids is not None and r['p%d_steamid64' % other] not in opp_ids: ok = False
                if f.get('result') == 'win' and r['winner'] != me - 1: ok = False
                if f.get('result') == 'loss' and r['winner'] != other - 1: ok = False
                for key, col in (('min_level', me), ('max_level', me), ('opponent_min_level', other), ('opponent_max_level', other)):
                    lv = r['p%d_level' % col]
                    if key in f and (lv is None or (lv < f[key] if 'min' in key else lv > f[key])): ok = False
            if ok:
                ok_any = True
                break
        if not ok_any: continue
        if 'p1_character' in f and r['p1_toon'] != f['p1_character']: continue
        if 'p2_character' in f and r['p2_toon'] != f['p2_character']: continue
        if 'winner' in f and r['winner'] != f['winner'] - 1: continue
        if not sided:
            if 'min_level' in f and not (r['p1_level'] is not None and r['p2_level'] is not None and r['p1_level'] >= f['min_level'] and r['p2_level'] >= f['min_level']): continue
            if 'max_level' in f and not (r['p1_level'] is not None and r['p2_level'] is not None and r['p1_level'] <= f['max_level'] and r['p2_level'] <= f['max_level']): continue
        if 'from' in f and (r['datetime_'] is None or r['datetime_'] < f['from']): continue
        if 'to' in f and (r['datetime_'] is None or r['datetime_'] > f['to']): continue
        if 'recorder' in f and r['recorder_steamid64'] != f['recorder']: continue
        out.append(r)
    col = 'upload_datetime_' if f.get('sort') == 'uploaded' else 'datetime_'
    out = [r for r in out if r[col] is not None]   # NULL dates sort apart; see the note in the doc
    out.sort(key=lambda r: (r[col], r['filename']), reverse=f.get('sort') != 'oldest')
    return out


def api_all(f, limit, max_pages):
    params = {k: (v.strftime('%Y-%m-%d %H:%M:%S') if isinstance(v, datetime.datetime) else v) for k, v in f.items()}
    got, cursor, pages, t = [], None, 0, time.time()
    while pages < max_pages:
        q = dict(params, limit=limit)
        if cursor: q['cursor'] = cursor
        r = client.get('/api/v2/replays', query_string=q)
        if r.status_code == 503:
            return 'timeout', time.time() - t, pages
        if r.status_code != 200:
            return None, r.status_code, r.data[:200]
        j = r.get_json(); got += j['replays']; cursor = j['next_cursor']; pages += 1
        if not cursor: break
    return got, time.time() - t, pages


rng = random.Random(7)
cur.execute("SELECT steamid64 FROM players WHERE replays >= 40 ORDER BY RAND(5) LIMIT 300")
PLAYERS = [r[0] for r in cur.fetchall()]

# ---- 1. replays
if '1' in RUN:
    print('\n1. /api/v2/replays against the reference')
    dates = sorted(r['datetime_'] for r in ROWS if r['datetime_'])
    cases = [
        {'player': 76561198083330827, 'character': 0},
        {'player': 76561198083330827, 'character': 0, 'opponent_character': 24, 'result': 'win'},
        {'character': 24, 'opponent_character': 0, 'min_level': 150},
        {'name': 'heythan'},
        {'name': 'kam', 'opponent_name': 'heythan'},
        {'character': 0, 'opponent_character': 24, 'result': 'win', 'min_level': 100, 'opponent_min_level': 100},
        {'p1_character': 3, 'p2_character': 22, 'sort': 'oldest'},
        {'character': 35, 'sort': 'uploaded'},
    ]
    for _ in range(54):
        f = {}
        if rng.random() < 0.6: f['player'] = rng.choice(PLAYERS)
        if rng.random() < 0.5: f['character'] = rng.randrange(36)
        if rng.random() < 0.3: f['opponent_character'] = rng.randrange(36)
        if 'player' in f and rng.random() < 0.3: f['side'] = rng.choice(['1', '2'])
        if rng.random() < 0.3: f['result'] = rng.choice(['win', 'loss'])
        if rng.random() < 0.15: f['opponent_min_level'] = rng.choice([20, 60])
        if rng.random() < 0.25: f['min_level'] = rng.choice([20, 50, 100])
        if rng.random() < 0.2: f['max_level'] = rng.choice([30, 80])
        if rng.random() < 0.3:
            a, b = sorted(rng.sample(dates, 2)); f['from'] = a; f['to'] = b
        if rng.random() < 0.15: f['winner'] = rng.choice([1, 2])
        if rng.random() < 0.3: f['sort'] = rng.choice(['oldest', 'uploaded'])
        if not any(k in f for k in ('player', 'character', 'opponent_character')):
            f['character'] = rng.randrange(36)
        cases.append(f)
    slow = []; refused = []
    for f in cases:
        want = ref_query(f)
        pages = 3 if len(want) > 2000 else 50
        got, secs, n_pages = api_all(f, 100, pages)
        if got == 'timeout':
            refused.append(f); continue
        if got is None:
            check(False, 'error %s %s for %s' % (secs, n_pages, f)); continue
        want_names = [r['filename'] for r in want][:len(got)]
        check([g['filename'] for g in got] == want_names, 'rows differ for %s (got %d, want %d)' % (f, len(got), len(want)))
        if len(want) <= 100 * pages:
            check(len(got) == len(want), 'not all rows for %s: %d of %d' % (f, len(got), len(want)))
        resp = client.get('/api/v2/replays', query_string=dict({k: (v.strftime('%Y-%m-%d %H:%M:%S') if isinstance(v, datetime.datetime) else v) for k, v in f.items()}, limit=1, count=1))
        if resp.status_code == 503:
            refused.append(dict(f, what='count'))
        else:
            r = resp.get_json()
            check(r['count'] == min(len(want), 10000), 'count %d != %d for %s' % (r['count'], len(want), f))
        slow.append((secs / max(n_pages, 1), f))
    print('  %d filter combinations, slowest page %.2f s, median %.3f s; refused as too broad (5 s limit): %d' % (
        len(cases), max(s for s, _ in slow), sorted(s for s, _ in slow)[len(slow) // 2], len(refused)))
    for f in refused:
        print('    refused:', f)
    for s, f in sorted(slow, key=lambda x: -x[0])[:3]:
        print('    %.2f s per page: %s' % (s, f))
    # a mirror match (same character both sides) appears once
    r = client.get('/api/v2/replays', query_string={'character': 0, 'opponent_character': 0, 'limit': 500}).get_json()
    fn = [x['filename'] for x in r['replays']]
    check(len(fn) == len(set(fn)), 'a mirror match listed twice')
    # bad input is refused, not run
    for bad in ({'player': 'x'}, {'from': '2026-13-45'}, {'sort': 'evil'}, {'limit': 100000}, {'result': 'win'}, {'name': 'a'}, {'cursor': 'nonsense'}, {'opponent_name': 'b'}):
        check(client.get('/api/v2/replays', query_string=bad).status_code == 400, 'bad input accepted: %s' % bad)
    r = client.get('/api/v2/replays', query_string={'name': "x' OR '1'='1"}).get_json()
    check(r['replays'] == [], 'a quote in a name matched something')

# ---- 2. players
if '2' in RUN:
    print('\n2. players, characters, filenames')
    r = client.get('/api/v2/players', query_string={'q': 'heythan'}).get_json()
    check(any(p['steamid64'] == '76561198083330827' for p in r['players']), 'Heythan not found by name')
    for sid in rng.sample(PLAYERS, 25):
        j = client.get('/api/v2/players/%d' % sid).get_json()
        per = collections.Counter(); wins = collections.Counter()
        for row in ROWS:
            for me in (1, 2):
                if row['p%d_steamid64' % me] == sid:
                    per[row['p%d_toon' % me]] += 1; wins[row['p%d_toon' % me]] += row['winner'] == me - 1
        got = {c['character']: (c['replays'], c['wins']) for c in j['characters']}
        check(got == {c: (per[c], wins[c]) for c in per}, 'player %d characters differ' % sid)
        # every replay of that player on their most played character, in one answer
        if per:
            toon = per.most_common(1)[0][0]
            want = sorted((row['filename'], me, row['p%d_toon' % (3 - me)], row['p%d_level' % me], row['p%d_level' % (3 - me)],
                           1 if row['winner'] == me - 1 else 0)
                          for row in ROWS for me in (1, 2) if row['p%d_steamid64' % me] == sid and row['p%d_toon' % me] == toon)
            rj = client.get('/api/v2/players/%d/replays' % sid, query_string={'character': toon}).get_json()
            got_rows = sorted((r[0], r[1], r[2], r[4], r[5], r[6]) for r in rj['replays'])
            check(rj['count'] == len(want) and got_rows == want, 'player %d replays on character %d differ' % (sid, toon))
            dates = [r[3] for r in rj['replays']]
            check(dates == sorted(dates, reverse=True), 'player %d replays not newest first' % sid)
    j = client.get('/api/v2/characters/0/players', query_string={'sort': 'replays', 'limit': 5}).get_json()
    check(j['players'] and j['players'][0]['replays'] >= j['players'][-1]['replays'], 'character players not sorted')
    names, after = [], ''
    for _ in range(3):
        j = client.get('/api/v2/filenames', query_string={'after': after, 'limit': 10000}).get_json()
        names += j['filenames']; after = j['next_after']
    check(names == sorted(r['filename'] for r in ROWS)[:30000], 'filenames walk differs')

# ---- 3. the old endpoint, against the server's code
if '3' in RUN:
    if len(sys.argv) > 1:
        print('\n3. /get_replays against the current server code at', sys.argv[1])
        for q in ['', 'p1_toon=0', 'p1_steamid64=76561198083330827', 'player_x=heythan', 'player_x=kam&player_y=heythan', 'p2_toon=24&page=3',
                  'winner=1&page_size=50', 'recorder=Kam']:
            new = client.get('/get_replays?' + q).data
            old = urllib.request.urlopen(sys.argv[1] + '/get_replays?' + q, timeout=600).read()
            old = json.loads(old)
            for r in old.get('results', []):   # the server's SELECT * would also return the new columns
                r.pop('p1_level', None); r.pop('p2_level', None)
            check(json.loads(new) == old, '/get_replays?%s differs' % q)

# ---- 4. upload
if '4' in RUN:
    print('\n4. /upload')
    tar_path = os.environ.get('REPLAY_TAR')
    if tar_path:
        with tarfile.open(tar_path, 'r|*') as t:
            for m in t:
                if m.isfile():
                    data = gzip.decompress(t.extractfile(m).read()); break
        fname = backend.get_hashed_filename(data)
        cur.execute("SELECT * FROM replay_metadata WHERE filename = ?", (fname,)); before = cur.fetchone()
        meta = backend.parse_replay_metadata(data, fname)
        cur.execute("DELETE FROM replay_metadata WHERE filename = ?", (fname,)); db.commit()
        def summary():
            out = []
            for side in (1, 2):
                cur.execute("SELECT replays, wins FROM player_characters WHERE steamid64 = ? AND toon = ?", (meta['p%d_steamid64' % side], meta['p%d_toon' % side]))
                out.append(cur.fetchone())
            return out
        s0 = summary()
        check(client.post('/upload', data=data).status_code == 200, 'upload refused')
        cur.execute("SELECT * FROM replay_metadata WHERE filename = ?", (fname,)); after = cur.fetchone()
        same = lambda r: [v for k, v in enumerate(r[:12]) if k != 2]   # all but upload_datetime_ (now)
        check(after is not None and (same(after) == same(before) if before else True), 'uploaded row differs from the original')
        check(after is not None and after[12] == meta['p1_level'] and after[13] == meta['p2_level'], 'levels not stored')
        if before:
            print('  levels stored %s/%s (from the file: %s/%s)' % (after[12], after[13], before[12], before[13]))
        s1 = summary()
        check(all(b[0] == a[0] + 1 for a, b in zip(s0, s1)), 'summaries did not move by one: %s -> %s' % (s0, s1))
        client.post('/upload', data=data)
        check(summary() == s1, 'a duplicate upload changed the summaries')
        check(os.path.exists(os.path.join(backend.app.config['UPLOAD_FOLDER'], fname + '.gz')), 'file not stored')
    else:
        print('  (REPLAY_TAR not set: skipped)')

# ---- 5. downloads
if '5' in RUN:
    print('\n5. downloads and bundles')
    folder = backend.app.config['UPLOAD_FOLDER']
    files = sorted(f[:-3] for f in os.listdir(folder) if f.endswith('.dat.gz'))[:150]
    if files:
        import io as _io
        f0 = files[0]
        raw = gzip.decompress(open(os.path.join(folder, f0 + '.gz'), 'rb').read())
        plain = client.get('/download/' + f0)
        check(plain.status_code == 200 and plain.data == raw and 'Content-Encoding' not in plain.headers, 'plain download changed')
        if len(sys.argv) > 1:
            old = urllib.request.urlopen(sys.argv[1] + '/download/' + f0, timeout=60).read()
            check(old == plain.data, 'plain download differs from the current server code')
        gz = client.get('/download/' + f0, headers={'Accept-Encoding': 'gzip, deflate'})
        check(gz.headers.get('Content-Encoding') == 'gzip' and gzip.decompress(gz.data) == raw, 'gzip download wrong')
        check(len(gz.data) < len(raw), 'gzip download not smaller')
        r = client.get('/api/v2/bundle', query_string={'files': ','.join(files[:100])})
        check(r.status_code == 200, 'bundle refused')
        with tarfile.open(fileobj=_io.BytesIO(r.data)) as t:
            got = {m.name: t.extractfile(m).read() for m in t.getmembers()}
        check(sorted(got) == sorted(n + '.gz' for n in files[:100]), 'bundle members differ')
        check(all(gzip.decompress(v) == gzip.decompress(open(os.path.join(folder, k), 'rb').read()) for k, v in got.items()), 'bundle content differs')
        print('  100 replays: %d bytes in one bundle, %d bytes as 100 plain downloads' % (len(r.data), sum(len(gzip.decompress(v)) for v in got.values())))
        check(client.get('/api/v2/bundle', query_string={'files': ','.join(files[:101]) + ',x.dat'}).status_code == 400, 'over 100 accepted')
        check(client.get('/api/v2/bundle', query_string={'files': '../etc/passwd'}).status_code == 400, 'path accepted')
        r = client.get('/api/v2/bundle', query_string={'files': 'aaaaaaaaaaaaaaaaaaaaaaaaa.dat,' + files[0]})
        with tarfile.open(fileobj=_io.BytesIO(r.data)) as t:
            check([m.name for m in t.getmembers()] == [files[0] + '.gz'], 'missing file not left out')
        # POST: the names in the body, up to 1000; gzip over the whole tar when asked for
        many = sorted(f[:-3] for f in os.listdir(folder) if f.endswith('.dat.gz'))[:1000]
        r = client.post('/api/v2/bundle', data='\n'.join(many), headers={'Accept-Encoding': 'gzip'})
        check(r.status_code == 200 and r.headers.get('Content-Encoding') == 'gzip', 'post bundle refused or not gzip')
        with tarfile.open(fileobj=_io.BytesIO(gzip.decompress(r.data))) as t:
            got = {m.name: t.extractfile(m).read() for m in t.getmembers()}
        check(sorted(got) == sorted(n + '.gz' for n in many), 'post bundle members differ')
        check(all(v == open(os.path.join(folder, k), 'rb').read() for k, v in got.items()), 'post bundle content differs')
        print('  %d replays: %d bytes in one POST bundle' % (len(many), len(r.data)))
        check(client.post('/api/v2/bundle', data=','.join(many + ['x%d.dat' % i for i in range(1001 - len(many))])).status_code == 400, 'over 1000 accepted')

print('\n%s: %d failures' % ('PASS' if not failures else 'FAIL', len(failures)))
sys.exit(1 if failures else 0)

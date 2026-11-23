"""Reads both players' ranks from every stored replay file and writes them as SQL for the
upgrade's work table (levels_import): INSERT batches of 5,000 rows on stdout, progress on stderr.

  python3 read_levels.py /root/flask_app/bbcf-im-replay-server/replays > levels.sql

Only the first bytes of each file are decompressed (the ranks are at 0x314 / 0x318, stored as
rank - 1). Rank rule, the same as the backend uses for new uploads: stored 1..39 -> LV2..Ruler;
0 (LV1: never ranked up on that character) and anything above Ruler (corrupt or edited files)
-> NULL. Standard library only."""
import os, struct, sys, time, zlib

NEED = 0x31C


def ranks(path):
    with open(path, 'rb') as f:
        head = f.read(8192)
    if head[:2] == b'\x1f\x8b':
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        data = d.decompress(head, NEED)
        if len(data) < NEED:   # the first bytes did not hold enough: read the whole file
            with open(path, 'rb') as f:
                data = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(f.read(), NEED)
    else:
        data = head
    if len(data) < NEED:
        return None
    a, b = struct.unpack_from('<II', data, 0x314)
    return tuple(r + 1 if 0 < r < 40 else None for r in (a, b))


def main():
    folder = sys.argv[1]
    total = None
    t0 = time.time()
    batch, done, bad = [], 0, 0
    out = sys.stdout

    def flush():
        if batch:
            out.write('INSERT IGNORE INTO levels_import VALUES ' + ','.join(batch) + ';\n')
            batch.clear()

    for e in os.scandir(folder):
        if not e.is_file():
            continue
        name = e.name[:-3] if e.name.endswith('.gz') else e.name
        try:
            r = ranks(e.path)
        except (OSError, zlib.error):
            r = None
        if r is None:
            bad += 1
            continue
        if any(c in name for c in "'\\"):
            continue
        batch.append("('%s',%s,%s)" % (name, 'NULL' if r[0] is None else r[0], 'NULL' if r[1] is None else r[1]))
        done += 1
        if len(batch) >= 5000:
            flush()
        if done % 50000 == 0:
            rate = done / max(0.001, time.time() - t0)
            sys.stderr.write('   %d files read, %.0f a second\n' % (done, rate))
            sys.stderr.flush()
    flush()
    sys.stderr.write('   %d files read in %.0f s (%d unreadable, skipped)\n' % (done, time.time() - t0, bad))


main()

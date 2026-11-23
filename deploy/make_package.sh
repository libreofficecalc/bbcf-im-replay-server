#!/bin/bash
# Builds replay-server-upgrade.tar.gz from this branch: the files to install, the SQL, the
# checksums of the server's current files (the commit the server runs: 6b986e8).
set -e
cd "$(dirname "$0")/.."
OUT=deploy/build/replay-server-upgrade
rm -rf deploy/build; mkdir -p $OUT/files/backend $OUT/files/frontend $OUT/sql $OUT/tools
cp backend/api_v2.py backend/stats.py backend/app.py backend/models.py $OUT/files/backend/
cp frontend/alt_front.py $OUT/files/frontend/
cp migrations/*.sql $OUT/sql/
cp deploy/read_levels.py $OUT/tools/
cp deploy/upgrade.sh deploy/README.md $OUT/
for f in backend/app.py backend/models.py frontend/alt_front.py; do
  echo "$(git show 6b986e8:$f | sha256sum | cut -d' ' -f1) $f"
done > $OUT/original.sha256
chmod +x $OUT/upgrade.sh
(cd deploy/build && tar -czf replay-server-upgrade.tar.gz replay-server-upgrade)
echo "deploy/build/replay-server-upgrade.tar.gz"

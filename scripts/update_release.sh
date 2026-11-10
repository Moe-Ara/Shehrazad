#!/bin/bash
# Update /opt/shehrazad in place to a new release; keeps .env, data/ and properties.properties.
# Usage (on the VPS): update_release.sh <tarball> <new image tag>
# Rolls back to the previous source and image if the new version does not become ready.
set -u
TARBALL=$1
NEW=$2
cd /opt/shehrazad || exit 1
OLD=$(sed -n 's/^IMAGE_TAG=//p' .env)
BACKUP="/opt/shehrazad-src-$OLD.tgz"
echo "updating $OLD -> $NEW"

replace_source() {  # $1 = tarball to put in place
  find /opt/shehrazad -mindepth 1 -maxdepth 1 ! -name data ! -name .env ! -name properties.properties -exec rm -rf {} +
  tar -xzf "$1" -C /opt/shehrazad
}

tar -czf "$BACKUP" --exclude=./data --exclude=./.env --exclude=./properties.properties .
replace_source "$TARBALL"
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$NEW/" .env
if ! docker compose build -q; then
  echo "!!! build failed; restoring $OLD source (the running container was not touched)"
  replace_source "$BACKUP"
  sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$OLD/" .env
  exit 1
fi
docker compose up -d

for i in $(seq 1 30); do
  sleep 3
  if curl -fs localhost:8080/readyz >/tmp/readyz.json 2>/dev/null && grep -q '"ready": *true' /tmp/readyz.json; then
    echo "READY after $((i*3))s"; cat /tmp/readyz.json; echo
    docker ps --format '{{.Names}} {{.Image}} {{.Status}}'
    exit 0
  fi
done
echo "!!! $NEW not ready - rolling back to $OLD"
docker compose logs --tail 60
replace_source "$BACKUP"
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$OLD/" .env
docker compose up -d
exit 1

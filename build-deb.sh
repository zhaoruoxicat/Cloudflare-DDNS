#!/bin/bash
set -euo pipefail
base=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
stage=$(mktemp -d /tmp/cf-ddns-package.XXXXXXXX)
trap 'rm -rf -- "$stage"' EXIT
install -d -m 755 "$stage/DEBIAN" "$stage/usr/bin" "$stage/usr/lib/cf-ddns/cf_ddns" \
    "$stage/usr/lib/systemd/system" "$stage/usr/share/doc/cf-ddns" "$base/dist"
install -m 644 "$base/packaging/control" "$stage/DEBIAN/control"
for script in postinst prerm postrm; do
    install -m 755 "$base/packaging/$script" "$stage/DEBIAN/$script"
done
install -m 755 "$base/bin/cf-ddns" "$stage/usr/bin/cf-ddns"
install -m 644 "$base"/src/cf_ddns/*.py "$stage/usr/lib/cf-ddns/cf_ddns/"
install -m 644 "$base"/systemd/cf-ddns.{service,timer} "$stage/usr/lib/systemd/system/"
install -m 644 "$base/README.md" "$stage/usr/share/doc/cf-ddns/README.zh-CN.md"
install -m 644 "$base/LICENSE" "$stage/usr/share/doc/cf-ddns/copyright"
size=$(du -sk "$stage/usr" | cut -f1)
printf 'Installed-Size: %s\n' "$size" >> "$stage/DEBIAN/control"
# Root ownership in the archive without requiring root to build.
dpkg-deb --root-owner-group --build "$stage" "$base/dist/cf-ddns_0.1.1_all.deb"
(cd "$base/dist" && sha256sum cf-ddns_0.1.1_all.deb > SHA256SUMS)

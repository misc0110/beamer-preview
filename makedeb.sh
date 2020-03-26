#!/bin/bash
name=beamer-preview
VERSION=0.1
if test `whoami` != "root"; then echo "You need to run this target using fakeroot: fakeroot -u make deb"; exit 1; fi
mkdir -pv deb/usr/bin
mkdir -pv deb/usr/share/doc/$name/
cp $name.py deb/usr/bin/$name
mkdir -p deb/DEBIAN
sed "s/%VERSION%/$VERSION/" debian-control > deb/DEBIAN/control
echo "initial version" > deb/usr/share/doc/$name/changelog
echo "Copyright 2020, Michael Schwarz" > deb/usr/share/doc/$name/copyright
gzip -c -9 -n deb/usr/share/doc/$name/changelog > deb/usr/share/doc/$name/changelog.gz
rm deb/usr/share/doc/$name/changelog
chmod -R 0755 deb/usr
chmod 0644 deb/usr/share/doc/$name/copyright
chmod 0644 deb/usr/share/doc/$name/changelog.gz
chown -R root:root deb/
dpkg-deb --build deb
rm -rf deb
lintian deb.deb 
mkdir -p dist
mv deb.deb dist/${name}_${VERSION}_amd64.deb

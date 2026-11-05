pkg list
mkdir -p /sd/moybyte/carts/jet.moy /sd/moybyte/carts/doom.moy /sd/moybyte/carts/mine.moy /sd/moybyte/written/jet /sd/moybyte/written/doom /sd/moybyte/system
printf '{"title": "Jet", "id": "moybyte.jet"}' > /sd/moybyte/carts/jet.moy/manifest.json
printf 'pass\n' > /sd/moybyte/carts/jet.moy/main.py
printf '{"title": "Doom"}' > /sd/moybyte/carts/doom.moy/manifest.json
printf 'pass\n' > /sd/moybyte/carts/doom.moy/main.py
printf 'pass\n' > /sd/moybyte/carts/mine.moy/main.py
printf 'x' > /sd/moybyte/written/jet/options.cfg
printf 'x' > /sd/moybyte/written/doom/options.cfg
printf '{"version": 1, "carts": {"jet.moy": {"id": "moybyte.jet", "index": "", "name": "Jet", "version": 3, "module": null, "files": {}}, "doom.moy": {"id": "id.doom", "index": "", "name": "Doom \\u00e9t\\u00e9", "version": 1, "module": null, "files": {}}, "gone.moy": {"id": "moybyte.gone", "index": "", "name": "Gone", "version": 2, "module": null, "files": {}}}}' > /sd/moybyte/installed.json
printf 'consent = {"moybyte.jet": {"perms": ["network"], "off": []}, "doom": {"perms": [], "off": []}}\ntheme = "dusk"\n' > /sd/moybyte/system/settings
pkg list
pkg list extra
pkg remove -y moybyte.jet
pkg remove mine.moy nope
pkg list
cat /sd/moybyte/installed.json
ls /sd/moybyte/carts /sd/moybyte/written
settings get consent
pkg remove id.doom
cat /sd/moybyte/installed.json
settings
pkg install moybyte.jet
pkg remove

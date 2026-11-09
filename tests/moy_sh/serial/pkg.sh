pkg list
mkdir -p /sd/moybyte/carts/jet.moy /sd/moybyte/carts/doom.moy /sd/moybyte/carts/mine.moy /sd/moybyte/apps/jet /sd/moybyte/apps/doom /sd/moybyte/system
printf '{"title": "Jet", "id": "kenny.jet"}' > /sd/moybyte/carts/jet.moy/manifest.json
printf 'pass\n' > /sd/moybyte/carts/jet.moy/main.py
printf '{"title": "Doom"}' > /sd/moybyte/carts/doom.moy/manifest.json
printf 'pass\n' > /sd/moybyte/carts/doom.moy/main.py
printf 'pass\n' > /sd/moybyte/carts/mine.moy/main.py
printf 'x' > /sd/moybyte/apps/jet/options.cfg
printf 'x' > /sd/moybyte/apps/doom/options.cfg
printf '{"version": 1, "carts": {"jet.moy": {"id": "kenny.jet", "index": "", "name": "Jet", "version": 3, "module": null, "files": {}}, "doom.moy": {"id": "id.doom", "index": "", "name": "Doom \\u00e9t\\u00e9", "version": 1, "module": null, "files": {}}, "gone.moy": {"id": "kenny.gone", "index": "", "name": "Gone", "version": 2, "module": null, "files": {}}}}' > /sd/moybyte/installed.json
printf 'consent = {"kenny.jet": {"perms": ["network"], "off": []}, "doom": {"perms": [], "off": []}}\ntheme = "dusk"\n' > /sd/moybyte/system/settings
pkg list
pkg list extra
pkg remove -y kenny.jet
pkg remove mine.moy nope
pkg list
cat /sd/moybyte/installed.json
ls /sd/moybyte/carts /sd/moybyte/apps
settings get consent
pkg remove id.doom
cat /sd/moybyte/installed.json
settings
pkg install kenny.jet
pkg remove

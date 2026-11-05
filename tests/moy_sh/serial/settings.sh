settings
settings get theme
mkdir -p /sd/moybyte/system
printf 'theme = "dusk"\nvolume=3\nnot a row\n# a comment\nfavorites = ["/a.moy"]\n' > /sd/moybyte/system/settings
settings
settings get volume
settings set name Moy the Cat
settings set font_scale 3
settings set bad key 1
settings unset favorites
settings unset favorites
cat /sd/moybyte/system/settings
settings reload
settings frob
pkg list

pwd
echo $HOME
mkdir -p /etc; printf 'theme = "dusk"\n' > /etc/settings
cat /moy/system/settings
settings get theme
settings set volume 3
cat /etc/settings
mkdir -p .ssh; echo key > .ssh/authorized_keys; cat /moy/system/authorized_keys
ls /sd
pkg list

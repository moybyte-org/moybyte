# if, for, while, until, case, break and continue
if [ 1 -lt 2 ]; then echo less; elif true; then echo never; else echo no; fi
for i in 1 2 3 4; do [ $i = 2 ] && continue; [ $i = 4 ] && break; echo i=$i; done
n=0; while [ $n -lt 3 ]; do n=$((n + 1)); done; echo n=$n
until [ $n -eq 0 ]; do n=$((n - 1)); done; echo n=$n
for f in a.moy b.txt c; do case $f in *.moy) echo cart $f;; *.txt|*.md) echo doc $f;; *) echo other $f;; esac; done
false || echo or; true && echo and; ! false && echo not

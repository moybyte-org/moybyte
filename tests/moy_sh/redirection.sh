# redirection into the files tree
echo first > f.txt
echo second >> f.txt
cat f.txt
wc -l < f.txt
cat nosuch 2> err.txt; cat err.txt
echo both > o.txt 2>&1; cat o.txt
exec 3> g.txt; echo via3 >&3; exec 3>&-; cat g.txt
echo x > /nosuchdir/f; echo status=$?

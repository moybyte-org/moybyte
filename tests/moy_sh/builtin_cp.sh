echo data > a; mkdir d
cp a b; cat b
cp a b d; ls d
cp nosuch x; echo status=$?
cp a; echo usage=$?

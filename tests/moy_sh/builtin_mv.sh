echo data > a; mkdir d
mv a b; ls
mv b d; ls d
mv d e; ls
mv nosuch x; echo status=$?

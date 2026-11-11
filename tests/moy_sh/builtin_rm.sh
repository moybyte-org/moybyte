echo x > a; mkdir -p d/e; echo y > d/e/f
rm a; ls
rm d; echo dir=$?
rm -r d; ls
rm nosuch; echo status=$?
rm -f nosuch; echo forced=$?

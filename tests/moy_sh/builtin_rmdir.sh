mkdir a b; echo f > b/f
rmdir a; ls
rmdir b/f; echo notdir=$?

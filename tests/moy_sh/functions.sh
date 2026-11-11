# functions persist across lines, take arguments and return a status
greet() { echo "hello $1 ($#)"; return 3; }
greet world x
echo status=$?
count() { local n=0; for a in "$@"; do n=$((n + 1)); done; echo $n; }
count a b c
echo local-gone=[$n]
type greet

printf 'a\nb\n' > one; echo c > two
cat one two
echo piped | cat - one
cat nosuch one; echo status=$?

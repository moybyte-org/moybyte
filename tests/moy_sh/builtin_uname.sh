uname
uname -s
uname -a | wc -w
[ "`uname -s`" = "Haiku" ] || echo not-haiku

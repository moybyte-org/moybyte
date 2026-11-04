# a forked child's state is gone when it exits
v=outer; ( v=inner; cd /proc; echo in $v $(pwd) ); echo out $v $(pwd)
( exit 5 ); echo status=$?
f() { v=func; }; ( f ); echo after $v
echo $( v=sub; echo $v ) $v

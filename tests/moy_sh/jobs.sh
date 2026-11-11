# background programs are processes of their own; jobs, fg, kill, wait
daemon &
echo pid=$!
jobs -l
ps | grep daemon
kill %1
jobs
( echo shell-code ) &
wait; echo waited=$?
daemon &
echo 3 > /.interrupt; fg %1
echo fg=$?

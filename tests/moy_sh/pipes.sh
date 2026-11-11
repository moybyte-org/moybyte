# pipelines run stage by stage over pipe buffers
echo one two three | wc -w
printf 'b\na\nc\n' | grep -v a | wc -l
lines() { i=0; while [ $i -lt 3000 ]; do echo line $i; i=$((i + 1)); done; }
lines | tail -n 2
lines | wc -l
{ echo g1; echo g2; } | cat
false | true; echo last=$?

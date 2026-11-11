printf '1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n12\n' > n
tail n | wc -l
tail -n 2 n
printf 'no newline' | tail -n 1; echo
printf 'key\n' > k
[ -z "`tail -1c k 2>/dev/null`" ] && echo ends-in-newline
printf 'key' > j
tail -1c j; echo
tail -c 3 n

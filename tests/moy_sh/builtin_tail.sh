printf '1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n12\n' > n
tail n | wc -l
tail -n 2 n
printf 'no newline' | tail -n 1; echo

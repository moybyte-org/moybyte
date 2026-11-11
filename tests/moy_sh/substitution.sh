# command substitution is a child whose output the parent reads
echo $(echo inner) "$(echo two words)" `echo back`
n=$(printf 'a\nb\n' | wc -l); echo n=$n
x=$(cd /; pwd; exit 3); echo "x=$x status=$?"
echo nested=$(echo $(echo deep))

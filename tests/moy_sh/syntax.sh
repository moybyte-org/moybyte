# variables, quoting, parameter expansion and arithmetic
x=5; y="two words"; echo $x "$y" '$y'
echo ${#y} ${y%% *} ${y#* } ${unset:-default} ${x:+set}
echo $((x * 3 + 1)) $((7 / 2)) $((7 % 2)) $((1 << 4)) $((x > 2 ? 10 : 20))
set -- a "b c" d; echo $# "$2"; shift; echo "$@"
echo "tab	and \"quotes\"" \$HOME

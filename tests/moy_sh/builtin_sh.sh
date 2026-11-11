echo 'echo in-script "$@"' > s
sh s a b
sh -c 'echo $0 $1' n one
sh; echo usage=$?
sh nosuch; echo missing=$?

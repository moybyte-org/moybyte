# scripts by sh, by path and on the PATH, with their own arguments
echo 'echo script $0 $# "$1"; exit 7' > s.sh
sh s.sh a b; echo s=$?
./s.sh q; echo direct=$?
sh -c 'echo c $0 $1' name one
mkdir tools; echo 'echo from-path "$@"' > tools/tool
PATH=/tools:$PATH; tool x y
. ./s.sh; echo never

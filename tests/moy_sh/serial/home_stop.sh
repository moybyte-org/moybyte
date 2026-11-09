mkdir -p docs
cd docs
X=set
%stop
pwd
echo "x=[$X] home=$HOME"
cd
pwd
edit /n.txt
%stop
^X
pwd
exit
%stop
pwd

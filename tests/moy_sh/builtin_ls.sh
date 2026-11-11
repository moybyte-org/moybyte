mkdir -p d/sub; echo abc > d/f.txt; touch d/.hidden
ls d
ls -a d
ls -1 d
ls -l d | grep f.txt | wc -l
ls d/f.txt
ls nosuch; echo status=$?

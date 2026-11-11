# errors are reported and the session goes on
if then
echo after=$?
nosuch-command; echo nf=$?
cd /nowhere; echo cd=$?
echo ${x?unset here}; echo never
echo still here

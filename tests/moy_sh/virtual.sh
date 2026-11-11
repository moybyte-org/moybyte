# /dev and /proc are the OS's, the root lists them beside the store
ls /
ls /dev
cat /proc/1/status | grep kind
echo ping > /dev/uart1; cat /dev/uart1
echo x > /bin/new; echo bin=$?
cat /dev/null; echo null=$?

// The priorities Dropbear's log takes (moy_ssh_log); ESP-IDF has no syslog.
#ifndef MOY_SSH_SYSLOG_H
#define MOY_SSH_SYSLOG_H
#define LOG_EMERG 0
#define LOG_ALERT 1
#define LOG_CRIT 2
#define LOG_ERR 3
#define LOG_WARNING 4
#define LOG_NOTICE 5
#define LOG_INFO 6
#define LOG_DEBUG 7
#define LOG_PID 0x01
#define LOG_AUTHPRIV (10 << 3)
#endif

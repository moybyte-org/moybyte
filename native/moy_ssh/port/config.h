// Dropbear's config.h for the console, written by hand where a POSIX system
// runs configure: what ESP-IDF's newlib and lwIP have (and the host test
// build's libc, a superset of it).

#ifndef MOY_SSH_CONFIG_H
#define MOY_SSH_CONFIG_H

#define BUNDLED_LIBTOM 1
#define DISABLE_ZLIB 1
#define DISABLE_SYSLOG 1
#define DISABLE_PAM 1
#define DISABLE_LASTLOG 1
#define DISABLE_UTMP 1
#define DISABLE_UTMPX 1
#define DISABLE_WTMP 1
#define DISABLE_WTMPX 1
#define DISABLE_PUTUTLINE 1
#define DISABLE_PUTUTXLINE 1

#define HAVE_CRYPT 1                    // moy_ssh_crypt
#define HAVE_FORK 1                     // never called: keeps the vfork paths out
#define HAVE_DAEMON 1                   // never called: keeps compat.c's out
#define HAVE_BASENAME 1
#define HAVE_CLOCK_GETTIME 1
#define HAVE_GETADDRINFO 1
#define HAVE_FREEADDRINFO 1
#define HAVE_STRUCT_ADDRINFO 1
#define HAVE_STRUCT_SOCKADDR_STORAGE 1
#define HAVE_STRUCT_SOCKADDR_STORAGE_SS_FAMILY 1
#define HAVE_STRUCT_IN6_ADDR 1
#define HAVE_STRUCT_SOCKADDR_IN6 1
#define HAVE_INTTYPES_H 1
#define HAVE_LIBGEN_H 1
#define HAVE_NETDB_H 1
#define HAVE_NETINET_IN_H 1
#define HAVE_NETINET_TCP_H 1
#define HAVE_STDINT_H 1
#define HAVE_STDIO_H 1
#define HAVE_STDLIB_H 1
#define HAVE_STRINGS_H 1
#define HAVE_STRING_H 1
#define HAVE_SYS_SELECT_H 1
#define HAVE_SYS_SOCKET_H 1
#define HAVE_SYS_STAT_H 1
#define HAVE_SYS_TYPES_H 1
#define HAVE_UNISTD_H 1
#define HAVE_UINT8_T 1
#define HAVE_UINT16_T 1
#define HAVE_UINT32_T 1
#define HAVE_U_INT8_T 1
#define HAVE_U_INT16_T 1
#define HAVE_U_INT32_T 1
#define HAVE_STATIC_ASSERT 1
#define STDC_HEADERS 1

#ifdef ESP_PLATFORM
#define HAVE_STRLCPY 1                  // newlib's
#define HAVE_STRLCAT 1
#endif

#ifdef __linux__
// glibc's, on the host test build; lwIP has neither (fake-rfc2553.c)
#define HAVE_DECL_HTOLE64 1
#define HAVE_GAI_STRERROR 1
#endif

#define PACKAGE_NAME "dropbear"
#define PACKAGE_VERSION "2026.94"

#endif // MOY_SSH_CONFIG_H

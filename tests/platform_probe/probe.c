/*
 * The platform probe for <sys/platform> (#1089).
 *
 * It prints the whole platform file for the host it was compiled for, and
 * tests/unit/test_platform_files.py compares that text with the bundled file
 * byte for byte. To make a file: cc probe.c -o probe && ./probe > <file>.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <stddef.h>
#include <stdio.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#if defined(__APPLE__) && defined(__x86_64__)
#define PLATFORM "darwin_x86_64"
#define INODE64 "$INODE64"
#elif defined(__APPLE__) && defined(__aarch64__)
#define PLATFORM "darwin_arm64"
#define INODE64 ""
#elif defined(__linux__) && defined(__x86_64__)
#define PLATFORM "linux_x86_64"
#define INODE64 ""
#else
#error "no <sys/platform> file for this host"
#endif

#ifdef SO_NOSIGPIPE
#define NOSIGPIPE_OPTION SO_NOSIGPIPE
#else
#define NOSIGPIPE_OPTION 0
#endif

#ifdef MSG_NOSIGNAL
#define NOSIGNAL_FLAG MSG_NOSIGNAL
#else
#define NOSIGNAL_FLAG 0
#endif

static void section(const char *title) { printf("\n# %s\n", title); }

static void num(const char *name, long value, const char *doc) {
    printf("\n##: %s :##\npublic const i32 %s = %ld\n", doc, name, value);
}

static void sym(const char *name, const char *value, const char *doc) {
    printf("\n##: %s :##\npublic const string %s = \"%s\"\n", doc, name, value);
}

int main(void) {
    struct stat st;
    struct timeval tv;

    printf("##:\nThe platform constants for `%s`.\n\n", PLATFORM);
    printf("The compiler picks one file per platform and architecture for `use <sys/platform>`,\n");
    printf("and every file declares the same names. Each value comes from\n");
    printf("`tests/platform_probe/probe.c`, compiled on that platform.\n:##\n");

    section("open(2) flags and lseek(2) whences");
    num("O_RDONLY", O_RDONLY, "`open` flag: read only.");
    num("O_WRONLY", O_WRONLY, "`open` flag: write only.");
    num("O_RDWR", O_RDWR, "`open` flag: read and write.");
    num("O_CREAT", O_CREAT, "`open` flag: create the file if it does not exist.");
    num("O_TRUNC", O_TRUNC, "`open` flag: cut an existing file to zero length.");
    num("O_APPEND", O_APPEND, "`open` flag: every write goes to the end.");
    num("SEEK_SET", SEEK_SET, "`lseek` whence: from the start.");
    num("SEEK_CUR", SEEK_CUR, "`lseek` whence: from the current offset.");
    num("SEEK_END", SEEK_END, "`lseek` whence: from the end.");

    section("struct stat, and the file type bits of st_mode");
    num("STAT_SIZE", sizeof(struct stat), "The size of `struct stat` in bytes.");
    num("ST_MODE_OFFSET", offsetof(struct stat, st_mode), "The byte offset of `st_mode`.");
    num("ST_MODE_BYTES", sizeof(st.st_mode), "The width of `st_mode` in bytes.");
    num("ST_SIZE_OFFSET", offsetof(struct stat, st_size), "The byte offset of `st_size`, an `i64`.");
#ifdef __APPLE__
    num("ST_MTIME_OFFSET", offsetof(struct stat, st_mtimespec), "The byte offset of the modification time, a `struct timespec`.");
    num("ST_CTIME_OFFSET", offsetof(struct stat, st_ctimespec), "The byte offset of the status change time, a `struct timespec`.");
#else
    num("ST_MTIME_OFFSET", offsetof(struct stat, st_mtim), "The byte offset of the modification time, a `struct timespec`.");
    num("ST_CTIME_OFFSET", offsetof(struct stat, st_ctim), "The byte offset of the status change time, a `struct timespec`.");
#endif
    num("S_IFMT", S_IFMT, "The mask of the file type bits in `st_mode`.");
    num("S_IFREG", S_IFREG, "File type: a regular file.");
    num("S_IFDIR", S_IFDIR, "File type: a directory.");
    num("S_IFLNK", S_IFLNK, "File type: a symbolic link.");
    num("S_IFCHR", S_IFCHR, "File type: a character device.");
    sym("STAT_SYMBOL", "stat" INODE64, "The link name of `stat` with the 64-bit inode layout.");
    sym("LSTAT_SYMBOL", "lstat" INODE64, "The link name of `lstat` with the 64-bit inode layout.");
    sym("READDIR_SYMBOL", "readdir" INODE64, "The link name of `readdir` with the 64-bit inode layout.");
    num("DIRENT_NAME_OFFSET", offsetof(struct dirent, d_name), "The byte offset of `d_name` in `struct dirent`.");

    section("Clocks");
    num("CLOCK_REALTIME", CLOCK_REALTIME, "`clock_gettime` clock: the wall clock.");
    num("CLOCK_MONOTONIC", CLOCK_MONOTONIC, "`clock_gettime` clock: a clock that never goes back.");
    num("TIMESPEC_SIZE", sizeof(struct timespec), "The size of `struct timespec` in bytes.");
    num("TIMEVAL_SIZE", sizeof(struct timeval), "The size of `struct timeval` in bytes.");
    num("TIMEVAL_USEC_OFFSET", offsetof(struct timeval, tv_usec), "The byte offset of `tv_usec`.");
    num("TIMEVAL_USEC_BYTES", sizeof(tv.tv_usec), "The width of `tv_usec` in bytes.");

    section("Sockets");
    num("AF_UNSPEC", AF_UNSPEC, "Address family: any.");
    num("AF_INET", AF_INET, "Address family: IPv4.");
    num("AF_INET6", AF_INET6, "Address family: IPv6.");
    num("SOCK_STREAM", SOCK_STREAM, "Socket type: a stream (TCP).");
    num("SOCK_DGRAM", SOCK_DGRAM, "Socket type: datagrams (UDP).");
    num("IPPROTO_TCP", IPPROTO_TCP, "Protocol: TCP.");
    num("IPPROTO_UDP", IPPROTO_UDP, "Protocol: UDP.");
    num("SOL_SOCKET", SOL_SOCKET, "`setsockopt` level: the socket itself.");
    num("SO_REUSEADDR", SO_REUSEADDR, "Socket option: reuse a local address.");
    num("SO_RCVTIMEO", SO_RCVTIMEO, "Socket option: the receive timeout, a `struct timeval`.");
    num("SO_SNDTIMEO", SO_SNDTIMEO, "Socket option: the send timeout, a `struct timeval`.");
    num("SO_ERROR", SO_ERROR, "Socket option: read and clear the pending error.");
    num("SO_NOSIGPIPE", NOSIGPIPE_OPTION, "Socket option that stops SIGPIPE for the socket, or 0 where the platform has none.");
    num("MSG_NOSIGNAL", NOSIGNAL_FLAG, "`send` flag that stops SIGPIPE for one call, or 0 where the platform has none.");
    num("SHUT_RDWR", SHUT_RDWR, "`shutdown` how: both directions.");
    num("AI_PASSIVE", AI_PASSIVE, "`getaddrinfo` flag: an address to bind.");
    num("AI_NUMERICHOST", AI_NUMERICHOST, "`getaddrinfo` flag: the host is a numeric address.");
    num("NI_NUMERICHOST", NI_NUMERICHOST, "`getnameinfo` flag: answer the numeric address.");
    num("NI_MAXHOST", NI_MAXHOST, "The size of a host name buffer for `getnameinfo`.");
    num("INET6_ADDRSTRLEN", INET6_ADDRSTRLEN, "The size of a buffer for the text of an IPv6 address.");
    num("EAI_SYSTEM", EAI_SYSTEM, "The `getaddrinfo` answer that means: read `errno`.");
    num("SOCKADDR_IN_SIZE", sizeof(struct sockaddr_in), "The size of `struct sockaddr_in` in bytes.");
    num("SOCKADDR_IN6_SIZE", sizeof(struct sockaddr_in6), "The size of `struct sockaddr_in6` in bytes.");
    num("SOCKADDR_STORAGE_SIZE", sizeof(struct sockaddr_storage), "The size of `struct sockaddr_storage` in bytes.");
    num("SOCKADDR_FAMILY_OFFSET", offsetof(struct sockaddr_in, sin_family), "The byte offset of the address family in a `sockaddr`.");
    num("SOCKADDR_FAMILY_BYTES", sizeof(((struct sockaddr_in *)0)->sin_family), "The width of the address family in bytes.");
    num("SOCKADDR_PORT_OFFSET", offsetof(struct sockaddr_in, sin_port), "The byte offset of the port in a `sockaddr_in`.");
    num("ADDRINFO_SIZE", sizeof(struct addrinfo), "The size of `struct addrinfo` in bytes.");
    num("AI_FLAGS_OFFSET", offsetof(struct addrinfo, ai_flags), "The byte offset of `ai_flags`.");
    num("AI_FAMILY_OFFSET", offsetof(struct addrinfo, ai_family), "The byte offset of `ai_family`.");
    num("AI_SOCKTYPE_OFFSET", offsetof(struct addrinfo, ai_socktype), "The byte offset of `ai_socktype`.");
    num("AI_PROTOCOL_OFFSET", offsetof(struct addrinfo, ai_protocol), "The byte offset of `ai_protocol`.");
    num("AI_ADDRLEN_OFFSET", offsetof(struct addrinfo, ai_addrlen), "The byte offset of `ai_addrlen`.");
    num("AI_CANONNAME_OFFSET", offsetof(struct addrinfo, ai_canonname), "The byte offset of `ai_canonname`.");
    num("AI_ADDR_OFFSET", offsetof(struct addrinfo, ai_addr), "The byte offset of `ai_addr`.");
    num("AI_NEXT_OFFSET", offsetof(struct addrinfo, ai_next), "The byte offset of `ai_next`.");

    section("errno numbers");
    num("EPERM", EPERM, "errno: the operation is not permitted.");
    num("ENOENT", ENOENT, "errno: no such file or directory.");
    num("EINTR", EINTR, "errno: a signal interrupted the call.");
    num("EIO", EIO, "errno: an input or output error.");
    num("EBADF", EBADF, "errno: a bad file descriptor.");
    num("EAGAIN", EAGAIN, "errno: try again; on a blocking socket, a timeout.");
    num("EACCES", EACCES, "errno: permission denied.");
    num("EEXIST", EEXIST, "errno: the file exists.");
    num("ENOTDIR", ENOTDIR, "errno: not a directory.");
    num("EISDIR", EISDIR, "errno: is a directory.");
    num("EINVAL", EINVAL, "errno: an argument is not valid.");
    num("ENFILE", ENFILE, "errno: too many open files in the system.");
    num("EMFILE", EMFILE, "errno: too many open files in the process.");
    num("ENOSPC", ENOSPC, "errno: no space left on the device.");
    num("EPIPE", EPIPE, "errno: the other end of the pipe or socket is closed.");
    num("ENAMETOOLONG", ENAMETOOLONG, "errno: the file name is too long.");
    num("ELOOP", ELOOP, "errno: too many levels of symbolic links.");
    num("EINPROGRESS", EINPROGRESS, "errno: the operation is in progress.");
    num("EDESTADDRREQ", EDESTADDRREQ, "errno: a destination address is required.");
    num("EMSGSIZE", EMSGSIZE, "errno: the message is too long.");
    num("EPROTONOSUPPORT", EPROTONOSUPPORT, "errno: the protocol is not supported.");
    num("EAFNOSUPPORT", EAFNOSUPPORT, "errno: the address family is not supported.");
    num("EADDRINUSE", EADDRINUSE, "errno: the address is in use.");
    num("EADDRNOTAVAIL", EADDRNOTAVAIL, "errno: the address is not available.");
    num("ENETDOWN", ENETDOWN, "errno: the network is down.");
    num("ENETUNREACH", ENETUNREACH, "errno: the network cannot be reached.");
    num("ENETRESET", ENETRESET, "errno: the network dropped the connection.");
    num("ECONNABORTED", ECONNABORTED, "errno: the connection was aborted.");
    num("ECONNRESET", ECONNRESET, "errno: the peer reset the connection.");
    num("ENOTCONN", ENOTCONN, "errno: the socket is not connected.");
    num("ETIMEDOUT", ETIMEDOUT, "errno: the operation timed out.");
    num("ECONNREFUSED", ECONNREFUSED, "errno: the peer refused the connection.");
    num("EHOSTUNREACH", EHOSTUNREACH, "errno: the host cannot be reached.");
    return 0;
}

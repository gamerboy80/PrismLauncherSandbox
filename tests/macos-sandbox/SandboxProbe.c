// SPDX-License-Identifier: GPL-3.0-only
#include <jni.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>

JNIEXPORT jboolean JNICALL Java_SandboxProbe_nativeWrite(JNIEnv *env, jclass cls, jstring name) {
    const char *path = (*env)->GetStringUTFChars(env, name, 0);
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0600);
    (*env)->ReleaseStringUTFChars(env, name, path);
    if (fd < 0) return JNI_FALSE;
    int result = write(fd, "probe", 5) == 5;
    close(fd);
    return result ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jboolean JNICALL Java_SandboxProbe_nativeRead(JNIEnv *env, jclass cls, jstring name) {
    const char *path = (*env)->GetStringUTFChars(env, name, 0);
    int fd = open(path, O_RDONLY | O_NOFOLLOW);
    (*env)->ReleaseStringUTFChars(env, name, path);
    if (fd < 0) return JNI_FALSE;
    char byte;
    int result = read(fd, &byte, 1) >= 0;
    close(fd);
    return result ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jboolean JNICALL Java_SandboxProbe_nativeConnect(JNIEnv *env, jclass cls, jint port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return JNI_FALSE;
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons((unsigned short)port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    int result = connect(fd, (struct sockaddr *)&addr, sizeof(addr));
    close(fd);
    return result == 0 ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jboolean JNICALL Java_SandboxProbe_nativeListen(JNIEnv *env, jclass cls) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return JNI_FALSE;
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    int result = bind(fd, (struct sockaddr *)&addr, sizeof(addr));
    if (result == 0) result = listen(fd, 1);
    close(fd);
    return result == 0 ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jboolean JNICALL Java_SandboxProbe_nativeDNS(JNIEnv *env, jclass cls) {
    struct addrinfo *addresses = NULL;
    int result = getaddrinfo("example.com", NULL, NULL, &addresses);
    if (addresses) freeaddrinfo(addresses);
    return result == 0 ? JNI_TRUE : JNI_FALSE;
}
